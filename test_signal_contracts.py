"""Production-query, evidence and lifecycle regressions; no services or credentials."""
import ast
from collections import defaultdict
from datetime import date, datetime, timedelta
import json
import logging
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import duckdb
import test_auto_trading as fixtures
from market_data_contract import canonical_price_ctes, canonical_stock, quality_score, coverage, VERSION
from signal_recovery import opening_candles, breadth_evidence, IST
from signal_diagnostics import score_gates, session_policy, summarise_decisions
from signal_evidence import market_policy_rejection
from shadow_validation import StudyStore, ShadowObserver
from strategy_gate_study import selected

ROOT=Path(__file__).parent


class DailyPrices(unittest.TestCase):
    def setUp(self):
        self.db=duckdb.connect(); self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE raw_prices(isin VARCHAR,trade_date DATE,source VARCHAR,close DOUBLE,volume BIGINT,turnover_cr DOUBLE,delivery_pct DOUBLE)')

    def insert(self,source,day,close=100,isin='I'):
        self.db.execute('INSERT INTO raw_prices VALUES (?,?,?,?,1000,1,50)',[isin,day,source,close])

    def prices(self):
        return self.db.execute('WITH '+canonical_price_ctes()+' SELECT isin,trade_date,source,close FROM prices ORDER BY isin,trade_date').fetchall()

    def test_nse_history_never_spliced_with_bse(self):
        self.insert('NSE','2026-10-01',100); self.insert('BSE','2026-10-01',101)
        self.insert('BSE','2026-09-30',99); self.insert('BSE','2026-10-01',90,'BSEONLY')
        rows=self.prices()
        self.assertEqual([(r[0],r[2],r[3]) for r in rows],[('BSEONLY','BSE',90),('I','NSE',100)])

    def test_exact_reruns_collapse(self):
        for _ in range(3): self.insert('NSE','2026-10-01')
        self.assertEqual(len(self.prices()),1)

    def test_conflicting_reruns_fail_instead_of_arbitrary_latest(self):
        self.insert('NSE','2026-10-01',100); self.insert('NSE','2026-10-01',110)
        with self.assertRaisesRegex(Exception,'Conflicting'): self.prices()

    def test_conflict_error_identifies_key_and_values(self):
        self.insert('NSE','2026-10-01',100,isin='INE000000001')
        self.insert('NSE','2026-10-01',110,isin='INE000000001')
        with self.assertRaises(Exception) as caught: self.prices()
        for fragment in ('Conflicting keys=1','INE000000001','2026-10-01','NSE',
                         '"close_min":100.0','"close_max":110.0'):
            self.assertIn(fragment,str(caught.exception))

    def test_conflict_examples_bounded_and_null_differences_visible(self):
        for i in range(12):
            self.insert('NSE','2026-10-01',isin=f'I{i:02}')
            self.db.execute("INSERT INTO raw_prices VALUES (?, '2026-10-01', 'NSE', 100,1000,1,NULL)",[f'I{i:02}'])
        with self.assertRaises(Exception) as caught: self.prices()
        message=str(caught.exception)
        self.assertIn('Conflicting keys=12',message)
        self.assertIn('"delivery_present":1',message)
        self.assertIn('I09',message); self.assertNotIn('I10',message)

    def test_feature_query_failure_propagates_and_closes_connection(self):
        tree=ast.parse((ROOT/'scripts/compute_features.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='compute_price_features')
        con=Mock(); con.execute.side_effect=RuntimeError('Conflicting fixture prices')
        ns=dict(pd=__import__('pandas'),get_duckdb_con=lambda:con,build_path=lambda day:"'fixture.parquet'",
                timedelta=timedelta,date=date,canonical_price_ctes=canonical_price_ctes,
                PRICE_CONTRACT_VERSION=VERSION,logger=logging.getLogger('fixture'))
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'features','exec'),ns)
        with self.assertRaisesRegex(RuntimeError,'Conflicting fixture'):
            ns['compute_price_features'](date(2026,10,5))
        con.close.assert_called_once()

    def test_required_empty_date_fails_without_publishing(self):
        tree=ast.parse((ROOT/'scripts/compute_features.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='run_for_date')
        writer=Mock()
        ns=dict(date=date,compute_price_features=lambda day:__import__('pandas').DataFrame(),write_features_to_r2=writer)
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'features','exec'),ns)
        with self.assertRaisesRegex(RuntimeError,'No features for required date'):
            ns['run_for_date'](date(2026,10,5))
        self.assertEqual(ns['run_for_date'](date(2026,10,2),allow_empty=True)['status'],'no_data')
        writer.assert_not_called()

    def test_production_atr_uses_fourteen_distinct_nse_sessions(self):
        tree=ast.parse((ROOT/'scripts/intraday_signals.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='get_key_levels')
        sql=next(n.args[0].value for n in ast.walk(fn) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='execute')
        sql=sql.replace('%s','?').replace("(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata')::date","DATE '2026-10-05'")
        self.db.execute('CREATE SCHEMA investmitra')
        self.db.execute('CREATE TABLE investmitra.equity_prices(isin VARCHAR,trade_date DATE,source VARCHAR,open DOUBLE,high DOUBLE,low DOUBLE,close DOUBLE)')
        self.db.execute("CREATE TABLE investmitra.company_master AS SELECT 'I' AS isin, 'A' AS nse_symbol")
        for i in range(20):
            for source,spread in [('NSE',2),('BSE',1)]:
                self.db.execute('INSERT INTO investmitra.equity_prices VALUES (?,?,?,?,?,?,?)',
                    ['I',date(2026,9,1)+timedelta(days=i),source,100+i,100+i+spread,100+i-spread,100+i])
        row=self.db.execute(sql,[['A']]).fetchone()
        self.assertEqual(row[7],4); self.assertEqual(row[5],109.5)
        self.db.execute("DELETE FROM investmitra.equity_prices WHERE trade_date < DATE '2026-09-08'")
        self.assertIsNone(self.db.execute(sql,[['A']]).fetchone()[7])

    def test_actual_feature_query_counts_sessions_not_exchange_rows(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'prices.parquet'
            for i in range(25):
                for venue in ('NSE','BSE'):
                    self.insert(venue,date(2026,9,1)+timedelta(days=i),100+i+(venue=='BSE'),isin='INE000000001')
            self.db.execute('COPY raw_prices TO ? (FORMAT PARQUET)',[str(path)])
            tree=ast.parse((ROOT/'scripts/compute_features.py').read_text())
            fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='compute_price_features')
            con=duckdb.connect()
            ns=dict(pd=__import__('pandas'),get_duckdb_con=lambda:con, build_path=lambda day:repr(str(path)),
                    timedelta=timedelta,date=date,canonical_price_ctes=canonical_price_ctes,
                    PRICE_CONTRACT_VERSION=VERSION,logger=logging.getLogger('fixture'))
            exec(compile(ast.Module(body=[fn],type_ignores=[]),'features','exec'),ns)
            df=ns['compute_price_features'](date(2026,9,25))
            self.assertEqual(len(df),1)
            self.assertAlmostEqual(df.iloc[0]['ret_5d_pct'],(124/119-1)*100,places=4)
            self.assertEqual(df.iloc[0]['price_contract_version'],VERSION)


class CatalogContract(unittest.TestCase):
    def stock(self,**changes):
        return dict(dict(symbol='A',investmitra_score=70,screen_count=10,piotroski=6,graham=2,
                         cap='MICRO',avg_volume=1000,avg_traded=10000000,in_bulk_deal=True),**changes)

    def test_cap_alias_quality_and_bulk_are_route_independent(self):
        expected=canonical_stock(self.stock())
        dynamic=canonical_stock(dict(self.stock(),market_cap_category='MICRO',quality_score=99))
        self.assertEqual(expected,dynamic)
        self.assertEqual(expected['quality_score'],62.5)
        self.assertEqual(expected['cap'],expected['market_cap_category'])
        self.assertTrue(expected['bulk_deal'])

    def test_invalid_score_and_cap_are_not_invented(self):
        for score in (None,float('nan'),float('inf'),101,-1):
            with self.assertRaises((ValueError,TypeError)): canonical_stock(self.stock(investmitra_score=score))
        with self.assertRaises(ValueError): canonical_stock(self.stock(cap='Unknown'))

    def test_unknown_sector_is_reported_without_assigning_a_proxy(self):
        result=coverage({'A':self.stock(sector='Unknown')},{},{'Energy':'NSE:NIFTY ENERGY'})
        self.assertEqual(result['unmapped_sector'],['A']);self.assertEqual(result['missing_atr'],['A'])

    def test_actual_initial_and_dynamic_routes_preserve_identical_quality(self):
        tree=ast.parse((ROOT/'scripts/intraday_signals.py').read_text(encoding='utf-8'))
        names={'get_intraday_watchlist','get_dynamic_gappers','classify_gap'}
        nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        stock=canonical_stock(self.stock())
        now=datetime(2026,10,5,10,tzinfo=IST)
        ns=dict(get_signal_catalog=lambda ctx:[stock],get_nse_gainers_losers=lambda:(['A'],[]),
                get_rvol_baseline=lambda:{'A':1000},datetime=SimpleNamespace(now=lambda tz:now),
                IST=IST,GAP_THRESHOLDS={'momentum':.3},logger=logging.getLogger('fixture'))
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'routes','exec'),ns)
        initial=ns['get_intraday_watchlist']({})[0][0]
        kite=SimpleNamespace(quote=lambda symbols:{'NSE:A':dict(ohlc={'open':102,'close':100},last_price=102,volume=1000)})
        later=ns['get_dynamic_gappers'](kite,set(),{})[0]
        for name in ('quality_score','market_cap_category','screen_count','piotroski','graham','in_bulk_deal'):
            self.assertEqual(initial[name],later[name])


class ContextRecovery(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,5,10,0,tzinfo=IST)
        start=self.now.replace(hour=9,minute=15)
        self.bars=[dict(date=start+timedelta(minutes=i),open=100,high=101+i,low=99,close=100) for i in range(15)]

    def test_completed_window_recovers_without_using_930_candle(self):
        extra=dict(self.bars[-1],date=self.now.replace(hour=9,minute=30),high=10000)
        result=opening_candles(self.bars+[extra],self.now)
        self.assertEqual(result['high'],115);self.assertEqual(result['low'],99)
        self.assertEqual(result['span']['source'],'kite_completed_minute_candles')

    def test_missing_wrong_day_future_and_conflicting_candles_rejected(self):
        self.assertIsNone(opening_candles(self.bars[:-1],self.now))
        self.assertIsNone(opening_candles(self.bars,self.now.replace(hour=9,minute=29)))
        self.assertIsNone(opening_candles([dict(b,date=b['date']-timedelta(days=1)) for b in self.bars],self.now))
        self.assertIsNone(opening_candles(self.bars+[dict(self.bars[0],high=200)],self.now))

    def test_breadth_requires_real_same_day_timestamp(self):
        b={'NIFTY 50':dict(advances=40,declines=10,quote_at=self.now.isoformat())}
        self.assertEqual(breadth_evidence(b,self.now,1)['breadth'],100)
        self.assertEqual(breadth_evidence(b,self.now,-1)['breadth'],30)
        self.assertEqual(breadth_evidence(b,self.now+timedelta(seconds=121),1)['breadth_status'],'stale_or_future')
        del b['NIFTY 50']['quote_at']
        self.assertEqual(breadth_evidence(b,self.now,1)['breadth_status'],'unverified')

    def test_executor_rechecks_market_quote_age(self):
        sig=dict(signal_weights=dict(require_fresh_market_context=True),
                 details=dict(market_context=dict(status='fresh',quote_at=self.now.isoformat())))
        self.assertIsNone(market_policy_rejection(sig,self.now))
        self.assertIsNotNone(market_policy_rejection(sig,self.now+timedelta(seconds=121)))


class DecisionEvidence(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.ExecutionTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.e=self.f._make_signal_engine()

    def test_numeric_score_changes_do_not_spam_but_evaluation_continues(self):
        calls=[]
        def score(*args):
            calls.append(1);return 20+len(calls)*.1,dict(gap_type='continuation',rvol=2)
        self.e._compute_opportunity_score=score
        with self.assertLogs('integration',level='INFO') as logs:
            for _ in range(10): self.e._check_signal('A',100,1000,self.f.now,'momentum')
        self.assertEqual(len(calls),10);self.assertEqual(len(logs.output),1)

    def test_all_independent_failures_recorded_after_first_score_reject(self):
        self.e.shadow=SimpleNamespace(decision=Mock())
        self.e._compute_opportunity_score=lambda *args:(20,dict(gap_type='continuation',rvol=2))
        self.e._check_signal('A',100,1000,self.f.now,'momentum')
        record=self.e.shadow.decision.call_args.args[0]
        self.assertEqual(record['gates']['blended']['status'],'FAIL')
        self.assertEqual(record['gates']['rvol']['status'],'FAIL')
        self.assertEqual(record['gates']['sizing_profit']['status'],'NOT_EVALUATED')
        self.assertFalse(self.f.manager.state['trades'])

    def test_session_policy_explains_midday_discount_without_changing_floor(self):
        policy=session_policy('choppy',.8)
        self.assertEqual((policy['rvol_min'],policy['priority_min'],policy['blended_min']),(8,5,55))
        self.assertAlmostEqual(.4*60+.6*60*policy['opportunity_multiplier'],49.2)

    def test_opening_recovery_does_not_seed_hold_and_api_runs_outside_lock(self):
        self.e.gap_first_seen.clear();self.e._check_signal.__globals__['datetime']=SimpleNamespace(now=lambda tz:self.f.now)
        start=self.f.now.replace(hour=9,minute=15,second=0,microsecond=0)
        bars=[dict(date=start+timedelta(minutes=i),open=100,high=101,low=99,close=100) for i in range(15)]
        def history(*args):
            self.assertFalse(self.e._state_lock._is_owned());return bars
        self.e.kite=SimpleNamespace(historical_data=history)
        self.e._recover_opening_range()
        self.assertTrue(self.e.or_set['A']);self.assertFalse(self.e.gap_first_seen)

    def test_decision_storage_deduplicates_minutes_and_legacy_reports_still_work(self):
        with tempfile.TemporaryDirectory() as td:
            observer=ShadowObserver(Path(td)/'study.sqlite3')
            self.e.shadow=observer
            for _ in range(10):self.e._check_signal('A',100,1000,self.f.now,'momentum')
            observer.close();self.assertFalse(observer.failed)
            db=sqlite3.connect(observer.path)
            try:
                rows=[json.loads(r[0]) for r in db.execute('SELECT body FROM decisions')]
                self.assertEqual(len(rows),1)
                self.assertEqual(summarise_decisions(rows)['symbol_sessions'],1)
                self.assertEqual(rows[0]['gates']['execution_admission']['status'],'QUEUED_NOT_FILLED')
            finally:db.close()

    def test_ablation_keeps_unknown_evidence_unknown(self):
        self.assertIsNone(selected({},'without_rvol_and_priority'))
        gates={k:dict(status='PASS') for k in ('gap','blended','rvol','priority','gap_type','sector','atr')}
        gates['rvol']['status']='FAIL';gates['priority']['status']='FAIL'
        features=dict(gate_diagnostics=gates)
        self.assertFalse(selected(features,'without_rvol_floor'))
        self.assertTrue(selected(features,'without_rvol_and_priority'))

    def test_pre_score_samples_do_not_hide_later_scored_failures(self):
        row=dict(day='2026-10-05',strategy_id='test',symbol='A',session='momentum',observed_at=1,
                 gates={},outcome='hold pending',reason_code='hold pending')
        later=dict(row,observed_at=2,gates={'rvol':dict(status='FAIL')},reason_code='rvol')
        self.assertEqual(summarise_decisions([row,later])['failures'],{'rvol':1})


if __name__=='__main__': unittest.main()
