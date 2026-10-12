"""Completed-session startup separation: no broker writes and no overnight prices."""
import ast
import copy
from datetime import date, datetime, timedelta
import json
import io
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).parent/'scripts'))
import kite_startup as ks

TODAY=date(2026,10,12)
EXPECTED=date(2026,10,9)
HOLIDAYS={date(2026,10,2),date(2026,1,26)}


def candles(count=300):
    days=ks.session_dates(EXPECTED-timedelta(days=500),EXPECTED,HOLIDAYS)[-count:]
    return [dict(date=datetime.combine(d,datetime.min.time(),tzinfo=ks.IST),
                 open=100+i*.1,high=102+i*.1,low=99+i*.1,close=101+i*.1,volume=100000+i)
            for i,d in enumerate(days)]


def metadata(count=101):
    return {f'S{i}':dict(symbol=f'S{i}',isin=f'I{i}',sector='Industrials',cap='SMALL',
        company_name=f'Stock {i}',slow_residual=50,screen_count=10,piotroski=6,graham=3,
        slow_score_date='2026-10-06',screen_date='2026-10-06') for i in range(count)}


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(redirect_stdout(io.StringIO()))
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def bars(self):
        return ks.validate_candles(candles(),EXPECTED,HOLIDAYS)

    def test_expected_previous_session_and_slow_age(self):
        self.assertEqual(ks.previous_session(TODAY,HOLIDAYS),EXPECTED)
        self.assertEqual(ks.slow_cutoff(EXPECTED,HOLIDAYS),date(2026,10,6))
        self.assertEqual(ks.slow_cutoff(date(2026,10,5),HOLIDAYS),date(2026,9,29))

    def test_calendar_retries_then_caches(self):
        f=Mock(side_effect=[TimeoutError(),HOLIDAYS]);sleep=Mock()
        self.assertEqual(ks.calendar(TODAY,self.root,f,sleep),HOLIDAYS)
        self.assertEqual(f.call_count,2)
        self.assertEqual(ks.calendar(TODAY,self.root,Mock(side_effect=TimeoutError()),sleep),HOLIDAYS)

    def test_expired_calendar_blocks(self):
        ks.write_json(self.root/'calendar.json',dict(fetched='2026-10-01',source='NSE_CM',holidays=['2026-10-02']))
        with self.assertRaises(RuntimeError):ks.calendar(TODAY,self.root,Mock(side_effect=TimeoutError()),lambda _:None)

    def test_holiday_is_not_a_missing_bar(self):
        self.assertEqual(self.bars()[-1]['date'],'2026-10-09')

    def test_stale_last_bar_rejected(self):
        with self.assertRaisesRegex(ValueError,'previous-session'):ks.validate_candles(candles()[:-1],EXPECTED,HOLIDAYS)

    def test_incomplete_bar_rejected(self):
        rows=candles();rows[-1]['date']=datetime(2026,10,12,tzinfo=ks.IST)
        with self.assertRaisesRegex(ValueError,'future'):ks.validate_candles(rows,EXPECTED,HOLIDAYS)

    def test_missing_recent_session_rejected(self):
        rows=candles();del rows[-3]
        with self.assertRaisesRegex(ValueError,'holes'):ks.validate_candles(rows,EXPECTED,HOLIDAYS)

    def test_naive_timestamp_rejected(self):
        rows=candles();rows[-1]['date']=datetime(2026,10,9)
        with self.assertRaisesRegex(ValueError,'timezone'):ks.validate_candles(rows,EXPECTED,HOLIDAYS)

    def test_conflicting_duplicates_rejected(self):
        rows=candles();rows.append(dict(rows[-1],volume=1))
        with self.assertRaisesRegex(ValueError,'conflicting'):ks.validate_candles(rows,EXPECTED,HOLIDAYS)

    def test_identical_duplicates_collapse(self):
        rows=candles();self.assertEqual(len(ks.validate_candles(rows+[rows[-1]],EXPECTED,HOLIDAYS)),300)

    def test_invalid_ohlcv_rejected(self):
        for change in ({'volume':float('nan')},{'close':0},{'high':1},{'volume':1.5},{'volume':-1}):
            with self.subTest(change=change):
                rows=candles();rows[-1].update(change)
                with self.assertRaises(ValueError):ks.validate_candles(rows,EXPECTED,HOLIDAYS)

    def test_large_discontinuity_rejected(self):
        rows=candles();r=rows[-1]
        for k in ('open','high','low','close'):r[k]*=2
        with self.assertRaisesRegex(ValueError,'corporate-action'):ks.validate_candles(rows,EXPECTED,HOLIDAYS)

    def test_features_use_completed_days_only(self):
        f=ks.features(self.bars(),TODAY)
        self.assertAlmostEqual(f['levels']['atr14'],3)
        self.assertEqual(f['baseline']['last_date'],'2026-10-09')
        self.assertEqual(f['baseline']['provider'],'Kite')
        self.assertEqual(f['avg_volume'],f['research']['mean_volume'])

    def test_short_history_neutral_is_explicit(self):
        f=ks.features(ks.validate_candles(candles(40),EXPECTED,HOLIDAYS),TODAY)
        self.assertIsNone(f['momentum']['ret_252d_pct'])
        self.assertEqual(ks.rank_momentum({'S':dict(sector='X',inputs=f)})['S'],50)

    def test_sector_momentum_ranking(self):
        f=ks.features(self.bars(),TODAY)
        low=copy.deepcopy(f);high=copy.deepcopy(f)
        low['momentum']={k:1 for k in f['momentum']}
        high['momentum']={k:2 for k in f['momentum']}
        ranks=ks.rank_momentum({'L':dict(sector='X',inputs=low),'H':dict(sector='X',inputs=high)})
        self.assertEqual(ranks,{'L':0.,'H':100.})

    def test_cache_is_bound_to_token_and_isin(self):
        kite=Mock();kite.historical_data.return_value=candles()
        client=ks.CandleClient(kite,self.root,sleep=lambda _:None)
        client.history('S','I',1,EXPECTED,HOLIDAYS)
        client.history('S','I',1,EXPECTED,HOLIDAYS)
        self.assertEqual(kite.historical_data.call_count,1)
        client.history('S','NEW',1,EXPECTED,HOLIDAYS)
        client.history('S','NEW',2,EXPECTED,HOLIDAYS)
        self.assertEqual(kite.historical_data.call_count,3)
        args=kite.historical_data.call_args.args
        self.assertEqual(args[2].hour,23)

    def test_authentication_failure_does_not_retry(self):
        TokenException=type('TokenException',(Exception,),{})
        kite=Mock();kite.historical_data.side_effect=TokenException()
        with self.assertRaisesRegex(RuntimeError,'authentication'):
            ks.CandleClient(kite,self.root,sleep=lambda _:None).history('S','I',1,EXPECTED,HOLIDAYS)
        self.assertEqual(kite.historical_data.call_count,1)

    def test_network_failure_is_bounded(self):
        kite=Mock();kite.historical_data.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError):
            ks.CandleClient(kite,self.root,sleep=lambda _:None).history('S','I',1,EXPECTED,HOLIDAYS)
        self.assertEqual(kite.historical_data.call_count,3)

    def build(self, records=None, side_effect=None):
        records=records or metadata()
        instruments=[dict(tradingsymbol=s,instrument_token=i,exchange='NSE',segment='NSE',instrument_type='EQ') for i,s in enumerate(records)]
        with patch.object(ks,'calendar',return_value=HOLIDAYS),patch.object(ks,'load_metadata',return_value=records),patch.object(ks.CandleClient,'history',return_value=self.bars(),side_effect=side_effect):
            return ks.build_snapshot(Mock(),instruments,Mock(),TODAY,self.root)

    def test_build_preserves_dates_slow_component_and_evidence(self):
        snapshot=self.build()
        self.assertEqual(len(snapshot['stocks']),101)
        stock=snapshot['stocks']['S0']
        self.assertEqual(stock['slow_score_date'],'2026-10-06')
        self.assertEqual(stock['price_date'],'2026-10-09')
        self.assertEqual(stock['investmitra_score'],50) # tied mature sector ranks normalize to 0
        self.assertEqual(stock['momentum_status'],'computed')
        self.assertTrue(list(self.root.glob('2026-10-12_*.json')))
        self.assertEqual(ks.baselines(snapshot).metadata['S0']['provider'],'Kite')

    def test_inadequate_cohort_blocks_and_records_reason(self):
        with self.assertRaisesRegex(RuntimeError,'coverage'):self.build(side_effect=ValueError('missing data'))
        manifest=json.loads((self.root/'latest_attempt.json').read_text())
        self.assertEqual(manifest['valid'],0)
        self.assertEqual(len(manifest['excluded']),101)

    def test_catalog_preserves_events_and_preopen(self):
        snapshot=self.build()
        found=ks.catalog(snapshot,dict(results_3days={'S0'},bulk_deals={'S2'},preopen_prices={'S2':140}),{'S1'})
        names={s['symbol'] for s in found}
        self.assertNotIn('S0',names);self.assertNotIn('S1',names)
        row=next(s for s in found if s['symbol']=='S2')
        self.assertTrue(row['in_bulk_deal']);self.assertGreater(row['preopen_gap'],0)

    def test_quote_history_disagreement_blocks_new_entry(self):
        s=dict(input_version=ks.VERSION,prev_close=100)
        self.assertIsNone(ks.price_rejection(s,100))
        self.assertIn('disagree',ks.price_rejection(s,50))
        self.assertIsNone(ks.price_rejection({},50))

    def test_metadata_has_no_price_or_receipt_dependency(self):
        cur=Mock();cur.__enter__=Mock(return_value=cur);cur.__exit__=Mock(return_value=False)
        cur.fetchone.side_effect=[(date(2026,10,6),),(date(2026,10,6),)]
        cur.fetchall.return_value=[('S','I','Stock','Industrials','SMALL',70,80,10,6,3)]
        con=Mock();con.cursor.return_value=cur
        rows=ks.load_metadata(con,EXPECTED,HOLIDAYS)
        self.assertEqual(rows['S']['slow_residual'],50)
        queries=' '.join(c.args[0] for c in cur.execute.call_args_list)
        self.assertNotIn('equity_prices',queries);self.assertNotIn('receipt',queries)
        self.assertIn('COUNT(DISTINCT UPPER(TRIM(x.isin)))',queries)
        self.assertEqual(cur.execute.call_args_list[0].args[1][:2],(date(2026,10,6),EXPECTED))

    def test_stale_slow_score_is_not_accepted(self):
        cur=Mock();cur.__enter__=Mock(return_value=cur);cur.__exit__=Mock(return_value=False)
        cur.fetchone.return_value=(None,)
        con=Mock();con.cursor.return_value=cur
        with self.assertRaisesRegex(RuntimeError,'three sessions'):ks.load_metadata(con,EXPECTED,HOLIDAYS)

    def test_providers_share_snapshot_without_price_database(self):
        import intraday_signals as engine
        snapshot=self.build()
        with patch.object(engine,'STARTUP_INPUTS',snapshot),patch.object(engine.psycopg2,'connect',side_effect=AssertionError('no price DB')):
            self.assertEqual(engine.get_key_levels(['S0'])['S0'],snapshot['levels']['S0'])
            self.assertEqual(engine.get_rvol_baseline()['S0'],snapshot['volumes']['S0'])

    def test_service_outage_stops_after_five_symbols(self):
        calls=[]
        def failure(*args):
            calls.append(args)
            raise TimeoutError()
        with self.assertRaisesRegex(RuntimeError,'service unavailable'):
            self.build(side_effect=failure)
        self.assertEqual(len(calls),5)

    def test_comparison_uses_same_kite_baseline(self):
        from comparison_capture import ComparisonTape
        import intraday_signals as engine
        from types import SimpleNamespace
        snapshot=self.build()
        e=SimpleNamespace(strategy_id='test',all_stocks=snapshot['stocks'],startup_snapshot=snapshot,
            rvol_baseline=snapshot['volumes'], _evaluate_signal=engine.IntradayEngine._evaluate_signal)
        execution=Mock();execution.snapshot.return_value={'flat':True,'trades':[],'limits':{}}
        e.execution=execution
        # Capture the constructor's loader without starting a writer thread.
        with patch.object(ComparisonTape,'__init__',return_value=None) as init:
            ComparisonTape.start(e,[])
        loader=init.call_args.args[2]
        self.assertEqual(loader(),snapshot['research'])

    def test_preflight_kite_does_not_call_overnight_audit(self):
        import intraday_signals as engine
        snapshot=self.build()
        con=Mock();cur=Mock();cur.fetchone.return_value=(139,)
        con.cursor.return_value=cur
        env={'KITE_API_KEY':'test','CC_POSTGRES_URL':'test','INVESTMITRA_STARTUP_SOURCE':'kite'}
        with patch.dict(engine.os.environ,env),patch.object(engine,'API_KEY','test'),patch.object(engine,'ACCESS_TOKEN','test'),patch.object(engine,'EXECUTION_MODE','auto_paper'),patch.object(engine.psycopg2,'connect',return_value=con),patch.object(ks,'build_snapshot',return_value=snapshot) as build,patch('signal_input_readiness.audit',side_effect=AssertionError('overnight audit called')),patch.object(engine,'STARTUP_INPUTS',None):
            self.assertTrue(engine.preflight_check(Mock(),[]))
            self.assertIs(engine.STARTUP_INPUTS,snapshot)
            build.assert_called_once()

    def test_kite_failure_cannot_use_old_snapshot(self):
        import intraday_signals as engine
        con=Mock();cur=Mock();cur.fetchone.return_value=(139,);con.cursor.return_value=cur
        with patch.dict(engine.os.environ,{'KITE_API_KEY':'test','CC_POSTGRES_URL':'test','INVESTMITRA_STARTUP_SOURCE':'kite'}),patch.object(engine,'API_KEY','test'),patch.object(engine,'ACCESS_TOKEN','test'),patch.object(engine,'EXECUTION_MODE','auto_paper'),patch.object(engine.psycopg2,'connect',return_value=con),patch.object(ks,'build_snapshot',side_effect=RuntimeError('history unavailable')),patch.object(engine,'STARTUP_INPUTS',{'old':True}):
            self.assertFalse(engine.preflight_check(Mock(),[]))
            self.assertIsNone(engine.STARTUP_INPUTS)


if __name__=='__main__':unittest.main()
