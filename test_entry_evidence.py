"""Offline sector admission, exchange-matched RVOL and execution-path evidence."""
import ast
import copy
import contextlib
from datetime import date, timedelta
import io
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
import test_auto_trading as fixtures
from order_manager import entry_policy_rejection
from signal_evidence import (load_volume_baselines, volume_evidence, VolumeBaselines,
                             SECTOR_POLICY_DEFAULTS)
from trade_research import observe_trade, finish_observation, print_research


class SectorGateTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ExecutionTests(); self.f.setUp(); self.addCleanup(self.f.tearDown)

    def signal(self, direction='LONG', change=0):
        sig = self.f.signal(direction=direction)
        sig['signal_weights'] = dict(SECTOR_POLICY_DEFAULTS)
        sig['details'].update(sector_chg=change, sector_status='fresh',
            sector_index='NSE:NIFTY AUTO', sector_quote_at=self.f.now.isoformat())
        return sig

    def test_sector_boundaries_are_directional(self):
        for direction, change, allowed in [('LONG',-.3,True),('LONG',-.301,False),
                ('SHORT',.3,True),('SHORT',.301,False),('LONG',2,True),('SHORT',-2,True)]:
            with self.subTest(direction=direction, change=change):
                self.assertEqual(entry_policy_rejection(self.signal(direction,change),self.f.now) is None,allowed)

    def test_bad_or_unknown_sector_never_passes_configured_gate(self):
        for edit in [dict(sector_chg=None),dict(sector_chg=float('nan')),dict(sector_chg=float('inf')),
                     dict(sector_status='unmapped_sector'),dict(sector_status='missing'),dict(sector_index=None),
                     dict(sector_quote_at=None),dict(sector_quote_at='bad')]:
            with self.subTest(edit=edit):
                sig=self.signal();sig['details'].update(edit)
                self.assertIn('sector gate',entry_policy_rejection(sig,self.f.now))

    def test_timestamp_is_rechecked_at_executor_admission(self):
        sig=self.signal();sig['details']['sector_quote_at']=(self.f.now-timedelta(seconds=119)).isoformat()
        self.assertIsNone(entry_policy_rejection(sig,self.f.now))
        self.f.manager.offer(sig)
        self.f.now += timedelta(seconds=2)
        self.f.manager.step()
        self.assertFalse(self.f.manager.state['trades'])
        self.assertFalse(self.f.broker.actions)
        self.assertIn('sector gate',self.f.manager._candidate_messages['A'][0][1])

    def test_future_sector_quote_rejected(self):
        sig=self.signal();sig['details']['sector_quote_at']=(self.f.now+timedelta(seconds=1)).isoformat()
        self.assertIn('future',entry_policy_rejection(sig,self.f.now))

    def test_invalid_config_does_not_disable_gate(self):
        for limit in [None,'bad',float('nan')]:
            sig=self.signal();sig['signal_weights']['min_sector_chg_long']=limit
            self.assertIn('sector gate',entry_policy_rejection(sig,self.f.now))

    def test_engine_blocks_weak_sector_before_queue(self):
        e=self.f._make_signal_engine();e.signal_weights=dict(SECTOR_POLICY_DEFAULTS)
        details=self.signal(change=-3.11)['details'];details['rvol']=5
        e._compute_opportunity_score=lambda *args:(90,details)
        e._check_signal('A',100,1000,self.f.now,'momentum')
        self.assertTrue(self.f.manager.inbox.empty())
        self.assertIn('sector gate',e.signal_rejections['A'][0])

    def test_engine_to_executor_accepts_valid_frozen_sector_policy(self):
        e=self.f._make_signal_engine();e.signal_weights=dict(SECTOR_POLICY_DEFAULTS)
        details=self.signal(change=0)['details'];details['rvol']=5
        e._compute_opportunity_score=lambda *args:(90,details)
        e._check_signal('A',100,1000,self.f.now,'momentum')
        self.f.manager.step();self.f.manager.step()
        t=self.f.manager.snapshot()['trades']['A']
        self.assertEqual(t['orders'][0]['filled'],t['orders'][0]['qty'])
        self.assertEqual(t['signal']['signal_weights'],SECTOR_POLICY_DEFAULTS)

    def test_failed_weight_load_keeps_sector_defaults(self):
        node=next(n for n in ast.parse(Path('scripts/intraday_signals.py').read_text(encoding='utf-8')).body
                  if isinstance(n,ast.FunctionDef) and n.name=='load_signal_weights')
        ns=dict(psycopg2=SimpleNamespace(connect=Mock(side_effect=OSError('offline'))),
                NEON_URL='unused',logger=Mock(),SECTOR_POLICY_DEFAULTS=SECTOR_POLICY_DEFAULTS)
        exec(compile(ast.Module(body=[node],type_ignores=[]),'weights','exec'),ns)
        self.assertEqual(ns['load_signal_weights'](),SECTOR_POLICY_DEFAULTS)


class VolumeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        self.db.execute("ATTACH DATABASE ':memory:' AS investmitra")
        self.db.execute('CREATE TABLE investmitra.equity_prices (isin TEXT,trade_date TEXT,source TEXT,volume REAL)')
        self.db.execute('CREATE TABLE investmitra.company_master (isin TEXT,nse_symbol TEXT)')
        self.db.execute("INSERT INTO investmitra.company_master VALUES ('I','A')")
        db=self.db
        class Cursor:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def execute(self,sql,params):
                self.cur=db.execute(sql.replace('%s','?'),[p.isoformat() for p in params])
            def fetchall(self):
                return [(s,a,n,date.fromisoformat(first),date.fromisoformat(last))
                        for s,a,n,first,last in self.cur.fetchall()]
        self.conn=SimpleNamespace(cursor=Cursor)
        self.day=date(2026,10,1)

    def rows(self,*rows):
        self.db.executemany('INSERT INTO investmitra.equity_prices VALUES (?,?,?,?)',rows)

    def test_nse_only_previous_dates_one_value_per_session(self):
        self.rows(('I','2026-09-29','NSE',1000),('I','2026-09-29','BSE',10),
                  ('I','2026-09-30','NSE',3000),('I','2026-09-30','NSE',3000),
                  ('I','2026-10-01','NSE',99999),('I','2026-10-02','NSE',99999),
                  ('I','2026-08-31','NSE',99999))
        b=load_volume_baselines(self.conn,self.day)
        self.assertEqual(b,{'A':2000})
        self.assertEqual(b.metadata['A']['sample_days'],2)
        self.assertEqual(b.metadata['A']['last_date'],'2026-09-30')
        self.assertEqual(b.metadata['A']['source'],'NSE')
        self.assertFalse(b.metadata['A']['corporate_action_adjusted'])

    def test_bse_only_zero_negative_conflicting_dates_are_excluded(self):
        self.rows(('I','2026-09-29','BSE',1000),('I','2026-09-28','NSE',0),
                  ('I','2026-09-27','NSE',-20),('I','2026-09-30','NSE',100),('I','2026-09-30','NSE',200))
        self.assertEqual(load_volume_baselines(self.conn,self.day),{})

    def test_symbol_collision_across_isins_is_excluded(self):
        self.db.execute("INSERT INTO investmitra.company_master VALUES ('J','A')")
        self.rows(('I','2026-09-29','NSE',1000),('J','2026-09-29','NSE',1000))
        self.assertEqual(load_volume_baselines(self.conn,self.day),{})

    def test_numeric_baseline_and_provenance_explain_actual_rvol(self):
        f=fixtures.ExecutionTests();f.setUp();self.addCleanup(f.tearDown)
        b=VolumeBaselines({'A':3000},{'A':dict(source='NSE',sample_days=20)})
        d=volume_evidence(b,b.metadata,'A',1800,f.now)
        self.assertEqual(d['rvol_avg_daily_volume'],3000)
        self.assertEqual(d['rvol_elapsed_fraction'],45/375)
        self.assertEqual(d['rvol_expected_volume'],360)
        self.assertEqual(d['rvol'],5)
        self.assertEqual(d['rvol_live_volume'],1800)
        self.assertEqual(d['rvol_baseline_status'],'verified_nse')
        d=volume_evidence(b,b.metadata,'A',180000,f.now)
        self.assertEqual(d['rvol'],200);self.assertEqual(d['rvol_uncapped'],500)
        json.dumps(d,allow_nan=False)

    def test_missing_baseline_has_no_synthetic_volume(self):
        f=fixtures.ExecutionTests();f.setUp();self.addCleanup(f.tearDown)
        d=volume_evidence({}, {}, 'A', 1000, f.now)
        self.assertEqual(d['rvol'],0);self.assertIsNone(d['rvol_expected_volume'])

    def test_real_engine_records_baseline_evidence(self):
        f=fixtures.ExecutionTests();f.setUp();self.addCleanup(f.tearDown)
        e=f._make_signal_engine();del e._compute_opportunity_score
        e.rvol_provenance={'A':dict(source='NSE',sample_days=17,last_date='2026-09-09')}
        ns=e._compute_opportunity_score.__globals__
        class Clock:
            @staticmethod
            def now(tz=None):return f.now
        ns.update(datetime=Clock, SECTOR_INDEX_MAP={},classify_gap=lambda *args:('continuation',1))
        _,d=e._compute_opportunity_score('A',100,1000,2,'momentum')
        self.assertEqual(d['rvol'],8.33)
        self.assertEqual(d['rvol_avg_daily_volume'],1000)
        self.assertEqual(d['rvol_baseline']['sample_days'],17)
        self.assertEqual(d['rvol_baseline_status'],'verified_nse')


class TradePathTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.ExecutionTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.now=self.f.now
        self.t=dict(sign=1,orders=[dict(kind='ENTRY',filled=10,average=100)],
                    entry_at=self.now.isoformat(),first_fill_observed_at=self.now.isoformat(),
                    closed_at=None,initial_risk=2)

    def observe(self,price,seconds=0,**quote):
        now=self.now+timedelta(seconds=seconds)
        observe_trade(self.t,dict(last_price=price,timestamp=now,**quote),now)

    def close(self,price=101,seconds=120):
        self.t['orders'].append(dict(kind='EXIT',filled=10,average=price))
        self.t['closed_at']=(self.now+timedelta(seconds=seconds)).isoformat()
        self.observe(price,seconds)

    def test_long_excursions_are_sampled_directional_and_deduplicated(self):
        self.observe(100);self.observe(103,2);self.observe(97,4);self.observe(97,4)
        r=self.t['research']
        self.assertEqual(r['sampled_quotes'],3)
        self.assertEqual((r['mfe_per_share'],r['mae_per_share']),(3,-3))
        self.assertEqual((r['mfe_r'],r['mae_r']),(1.5,-1.5))
        self.assertEqual(r['coverage'],'sampled')

    def test_short_excursions_and_post_exit_move(self):
        self.t['sign']=-1;self.observe(95);self.observe(103,2);self.close(101)
        r=self.t['research'];self.assertEqual((r['mfe_per_share'],r['mae_per_share']),(5,-3))
        self.observe(98,120+900)
        m=r['post_exit']['15'];self.assertEqual(m['move_from_entry_per_share'],2)
        self.assertEqual(m['move_from_exit_vwap_per_share'],3)

    def test_partial_exits_use_vwap_without_inventing_full_position_pnl(self):
        self.observe(100)
        self.t['orders'] += [dict(kind='EXIT',filled=4,average=104),dict(kind='STOP',filled=6,average=99)]
        self.t['closed_at']=(self.now+timedelta(seconds=120)).isoformat()
        self.observe(99,120);self.observe(110,1020)
        r=self.t['research'];self.assertEqual(r['exit_vwap'],101)
        self.assertEqual(r['post_exit']['15']['move_from_exit_vwap_per_share'],9)
        self.assertNotIn('net',r['post_exit']['15'])
        self.assertEqual(r['high'],100) # post-exit movement cannot change during-trade extrema

    def test_no_fill_has_no_research(self):
        self.t['orders'][0]['filled']=0;self.observe(100)
        self.assertNotIn('research',self.t)

    def test_stale_and_invalid_quotes_never_become_zero_outcomes(self):
        self.observe(100)
        now=self.now+timedelta(seconds=60)
        for q in [{},dict(timestamp=now,last_price=float('nan')),dict(timestamp=self.now,last_price=150)]:
            observe_trade(self.t,q,now)
        r=self.t['research'];self.assertEqual(r['sampled_quotes'],1)
        self.assertEqual(r['coverage'],'gapped');self.assertEqual(r['high'],100)
        json.dumps(self.t,allow_nan=False)

    def test_exact_horizons_record_once_and_survive_restart(self):
        self.observe(100);self.close()
        self.observe(102,1019);self.assertEqual(self.t['research']['post_exit']['15']['status'],'PENDING')
        self.observe(103,1020)
        self.t=json.loads(json.dumps(self.t))
        self.observe(104,1022);self.observe(105,1920);self.observe(106,3720)
        r=self.t['research']['post_exit']
        self.assertEqual([r[str(m)]['price'] for m in (15,30,60)],[103,105,106])

    def test_missing_horizon_after_hibernate_is_not_backfilled(self):
        self.observe(100);self.close();self.observe(105,120+901+60)
        self.assertEqual(self.t['research']['post_exit']['15']['status'],'MISSING_FRESH_QUOTE')
        self.assertNotIn('price',self.t['research']['post_exit']['15'])
        self.assertEqual(self.t['research']['post_exit']['30']['status'],'PENDING')

    def test_session_end_censors_late_horizons(self):
        self.observe(100);self.close(seconds=4*3600+50*60)
        self.observe(102,5*3600+5*60)
        marks=self.t['research']['post_exit']
        self.assertEqual(marks['15']['status'],'COMPLETE')
        self.assertEqual(marks['30']['status'],'CENSORED_SESSION_END')
        self.assertEqual(marks['60']['status'],'CENSORED_SESSION_END')

    def test_early_stop_and_same_day_restart_resume_future_marks(self):
        self.observe(100);self.close()
        finish_observation({'A':self.t},self.now+timedelta(seconds=130))
        self.assertEqual(self.t['research']['post_exit']['15']['status'],'CAPTURE_STOPPED')
        self.observe(105,1020)
        self.assertEqual(self.t['research']['post_exit']['15']['status'],'COMPLETE')

    def test_execution_integration_persists_and_research_never_changes_orders(self):
        self.f.enter();t=self.f.manager.state['trades']['A']
        self.assertGreater(t['research']['sampled_quotes'],0)
        original=copy.deepcopy(self.f.broker.actions)
        with patch('order_manager.observe_trade',side_effect=ValueError('test research failure')):
            self.f.now+=timedelta(seconds=2)
            with self.assertLogs('order_manager',level='WARNING'):self.f.manager.step()
        self.assertEqual(self.f.broker.actions,original)
        self.assertEqual(t['research_error'],'ValueError')
        self.assertTrue(self.f.manager.ready)
        self.f.restart()
        self.assertEqual(self.f.manager.state['trades']['A']['research']['version'],'trade-path-v1')

    def test_actual_executor_exit_research_and_report_reach_shutdown_snapshot(self):
        self.f.enter();self.f.now+=timedelta(minutes=41);self.f.broker.price=99.9
        self.f.manager.step();self.f.manager.step()
        t=self.f.manager.state['trades']['A'];self.assertEqual(t['closed_reason'],'DEAD_TRADE')
        self.f.now+=timedelta(minutes=15);self.f.broker.price=101
        self.f.manager.step()
        self.assertEqual(t['research']['post_exit']['15']['status'],'COMPLETE')
        self.f.manager.finish_research()
        snapshot=self.f.manager.snapshot()
        self.assertEqual(snapshot['trades']['A']['research']['post_exit']['30']['status'],'CAPTURE_STOPPED')
        body=self.f.journal.db.execute('SELECT body FROM state WHERE id=1').fetchone()[0]
        saved=json.loads(body);self.assertEqual(saved['trades']['A']['research'],snapshot['trades']['A']['research'])
        out=io.StringIO()
        with contextlib.redirect_stdout(out):print_research(saved)
        self.assertIn('not realised',out.getvalue());self.assertIn('+15m price 101.00',out.getvalue())
        with patch.object(self.f.manager,'_mirror_snapshot_to_neon',return_value=True) as mirror:
            with patch.dict('os.environ',CC_POSTGRES_URL='test-only'):self.f.manager.mirror_to_neon()
        self.assertEqual(mirror.call_args.args[1]['trades']['A']['research'],t['research'])


if __name__=='__main__': unittest.main()
