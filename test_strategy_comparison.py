"""Actual signal/executor replay, offline isolation and tape integrity."""
import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
import queue
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent / 'scripts'))
import intraday_signals as engine_module
from order_manager import IST, Journal
from comparison_capture import (ComparisonTape, capture_frame, CONFIG_NAMES, VERSION,
                                read_frames, read_metadata, source_hashes, dumps, run_comparison_report)
from strategy_comparison import (VARIANTS, variant_policy, baseline_available, run,
                                PortfolioRun, QuoteTape, ReplayBroker, ReplayClock,
                                apply_inputs, validate_tape)


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = datetime(2026, 10, 7, 9, 30, tzinfo=IST)
        stock = dict(symbol='A', isin='I', sector='Technology', quality_score=90,
                     investmitra_score=80, market_cap_category='SMALL')
        self.engine = engine_module.IntradayEngine([stock], [], {'A': 1}, {'A': 98}, 'BULLISH',
            {'vix_signal': 'NORMAL'}, {'A': 10000},
            {'A': dict(atr14=3, ma20=95, ma50=94, high_52w=120, low_52w=70)}, {}, {})
        self.engine.rvol_provenance = {'A': dict(source='NSE', sample_days=15,
            first_date='2026-09-09', last_date='2026-10-06', as_of='2026-10-07')}
        self.engine.signal_weights = {'gap_score': .3, 'rvol_score': .15}
        self.engine.strategy_id = 'fixture'
        self.meta = dict(version=VERSION, day='2026-10-07', strategy_id='fixture',
            source_hashes=source_hashes(), config={k: copy.deepcopy(getattr(engine_module,k)) for k in CONFIG_NAMES},
            initial_execution_flat=True, initial_execution_trades=0,
            instruments=[dict(tradingsymbol='A', instrument_token=1, exchange='NSE', segment='NSE', tick_size=.05)],
            limits=dict(daily_cap=35000, min_ticket=1000, max_ticket=10000, max_risk=1500,
                        max_daily_loss=1500, max_positions=3, cost_reserve=80))
        self.baselines = {'A': dict(status='recomputed_daily_baseline', sessions=15,
            first_date='2026-09-09', last_date='2026-10-06', mean_volume=10000,
            median_volume=4000, excluded_conflicting_days=0)}
        self.meta['baselines'] = self.baselines

    def frame(self, at=None, price=100, volume=2000):
        now = at or self.now
        tick = dict(instrument_token=1, last_price=price, volume_traded=volume,
            average_traded_price=99, ohlc=dict(open=100, close=98),
            exchange_timestamp=now, last_trade_time=now, upper_circuit_limit=120)
        return capture_frame(self.engine, [tick], now)

    def portfolio(self, name='mean_gated', case='base'):
        p = PortfolioRun(engine_module, self.meta, self.frame(), VARIANTS[name], name,
                         Path(self.tmp.name)/(name+case+'.sqlite3'), case)
        self.addCleanup(p.journal.close)
        return p

    def test_policies_change_only_named_gates(self):
        for session, rvol, priority in [('momentum',5,3),('choppy',8,5),('afternoon',5,3)]:
            control = variant_policy(VARIANTS['mean_gated'], session,.3,3)
            self.assertEqual((control['rvol_min'],control['priority_min']), (rvol,priority))
            rank = variant_policy(VARIANTS['median_rank'],session,.3,3)
            self.assertEqual(rank, dict(control,priority_min=0.))
            trial = variant_policy(VARIANTS['median_rank_rvol2'],session,.3,3)
            self.assertEqual(trial['rvol_min'],3 if session=='choppy' else 2)
            self.assertEqual(trial['blended_min'],55)

    def test_baselines_require_prior_matched_nonconflicting_history(self):
        row = self.baselines['A']
        self.assertTrue(baseline_available(row,10000,self.meta['day']))
        for edit in [dict(sessions=9),dict(last_date='2026-10-07'),
                     dict(excluded_conflicting_days=1),dict(median_volume=0)]:
            self.assertFalse(baseline_available(dict(row,**edit),10000,self.meta['day']))
        self.assertFalse(baseline_available(row,9000,self.meta['day']))

    def test_capture_is_detached_and_does_not_copy_portfolio_state(self):
        self.engine.traded_today.add('A')
        frame = self.frame()
        self.engine.all_stocks['A']['quality_score'] = 1
        self.assertEqual(frame['symbols']['A']['all_stocks']['quality_score'],90)
        self.assertNotIn('traded_today', frame)
        json.loads(dumps(frame))

    def test_median_recomputes_gap_classification_and_blended_inputs(self):
        mean, median = self.portfolio(), self.portfolio('median_gated')
        f = self.frame(volume=700)
        for p in (mean,median):
            p.frame(f)
        a = mean.engine._compute_opportunity_score('A',100,700,2.1,'momentum')
        b = median.engine._compute_opportunity_score('A',100,700,2.1,'momentum')
        self.assertNotEqual(a[1]['rvol'],b[1]['rvol'])
        self.assertNotEqual(a[1]['gap_type'],b[1]['gap_type'])
        self.assertGreater(b[0],a[0])
        self.assertEqual(self.engine.rvol_baseline['A'],10000)

    def test_missing_median_excludes_symbol_in_control_too(self):
        p = self.portfolio()
        p.meta['baselines'] = {}
        p.frame(self.frame())
        self.assertIn('A',p.exclusions)
        self.assertNotIn('A',p.engine.rvol_baseline)

    def test_actual_engine_requires_hold_then_fills_partial_and_target(self):
        p = self.portfolio()
        for second in range(0, 310, 5):
            p.frame(self.frame(self.now+timedelta(seconds=second), volume=10000))
        self.assertIn('A',p.manager.state['trades'])
        t = p.manager.state['trades']['A']
        self.assertGreaterEqual(datetime.fromisoformat(t['entry_at']),self.now+timedelta(minutes=5))
        self.assertLessEqual(t['orders'][0]['filled']*t['orders'][0]['average'],10000)
        # Move just beyond 1R, then to the final target; use actual exit rules.
        for second in range(310, 330, 2):
            p.frame(self.frame(self.now+timedelta(seconds=second),price=105,volume=11000))
        self.assertTrue(t['partial_done'])
        for second in range(330, 350, 2):
            p.frame(self.frame(self.now+timedelta(seconds=second),price=110,volume=12000))
        self.assertEqual(t['exit_reason'],'TARGET')
        self.assertTrue(t['closed_at'])
        self.assertEqual(p.manager._remaining(t),0)
        self.assertGreater(p.result()['provisional_net'],0)
        self.assertGreater(p.manager.snapshot()['remaining'],34000)

    def test_quote_store_does_not_manufacture_freshness(self):
        q = QuoteTape(); f = self.frame()
        f['ticks'][0].pop('exchange_timestamp'); f['ticks'][0].pop('last_trade_time')
        q.update(f)
        self.assertIsNone(q.quote(['NSE:A'])['NSE:A']['timestamp'])

    def test_stale_quotes_never_fill_market_exit(self):
        p = self.portfolio()
        p.broker.last['NSE:A'] = dict(last_price=100,timestamp=self.now-timedelta(seconds=11))
        o = dict(status='OPEN',tradingsymbol='A',transaction_type='SELL',order_type='MARKET',quantity=10)
        p.broker._match(o)
        self.assertEqual(o['status'],'OPEN')
        self.assertEqual(p.broker.missing_price_attempts,1)

    def test_slippage_respects_limit_and_is_adverse_both_sides(self):
        p = self.portfolio()
        p.broker.last['NSE:A'] = dict(last_price=100,timestamp=self.now)
        for side, expected in [('BUY',100.05),('SELL',99.95)]:
            o = dict(status='OPEN',tradingsymbol='A',transaction_type=side,order_type='MARKET',quantity=10)
            p.broker._match(o); self.assertAlmostEqual(o['average_price'],expected)
        o = dict(status='OPEN',tradingsymbol='A',transaction_type='BUY',order_type='LIMIT',price=100.01,quantity=10,validity='IOC')
        p.broker._match(o); self.assertEqual(o['status'],'CANCELLED')

    def test_tape_roundtrip_and_full_runner_has_no_network(self):
        path = Path(self.tmp.name)/'tape.sqlite3'
        tape = ComparisonTape(path,self.meta,lambda:self.baselines)
        tape.queue.put(self.frame())
        tape.queue.put(self.frame(self.now+timedelta(seconds=5)))
        tape.close()
        self.assertFalse(tape.worker.is_alive())
        self.assertEqual(read_metadata(path)['frames'],2)
        self.assertEqual(list(read_frames(path))[0]['at'],self.now)
        with patch('requests.sessions.Session.request', side_effect=AssertionError('network')), \
             patch('psycopg2.connect', side_effect=AssertionError('database')), \
             patch('order_manager.notify', side_effect=AssertionError('alert')):
            result = run(path,engine_module)
        self.assertEqual(len(result['results']),10)
        self.assertFalse(result['full_session_span'])
        self.assertTrue(all(r['filled']==0 for r in result['results'].values()))

    def test_tape_failure_and_source_drift_fail_closed(self):
        meta = dict(self.meta,closed=True,frames=1,dropped=0,error=None)
        validate_tape(meta)
        for edit in [dict(closed=False),dict(dropped=1),dict(error='disk'),
                     dict(source_hashes={}),dict(initial_execution_trades=1)]:
            with self.assertRaises(ValueError): validate_tape(dict(meta,**edit))

    def test_disk_quota_marks_capture_incomplete(self):
        path = Path(self.tmp.name)/'limited.sqlite3'
        tape = ComparisonTape(path,self.meta,lambda:self.baselines,max_bytes=1)
        tape.queue.put(self.frame()); tape.close()
        self.assertTrue(tape.failed)
        with self.assertRaises(ValueError): validate_tape(read_metadata(path))

    def test_loader_failure_is_research_only(self):
        path = Path(self.tmp.name)/'failed.sqlite3'
        tape = ComparisonTape(path,self.meta,Mock(side_effect=OSError('offline')))
        tape.close()
        self.assertEqual(tape.failed,'OSError')
        self.assertTrue(read_metadata(path)['error'])
        self.assertFalse(self.engine.signals)

    def test_full_queue_never_blocks_or_mutates_engine(self):
        tape = ComparisonTape.__new__(ComparisonTape)
        tape.failed = None; tape.stop = threading.Event(); tape.dropped = 0
        tape.queue = queue.Queue(maxsize=1); tape.queue.put('full')
        original = copy.deepcopy(self.engine.all_stocks)
        tape.capture(self.engine,self.frame()['ticks'],self.now)
        self.assertEqual(tape.dropped,1)
        self.assertEqual(self.engine.all_stocks,original)

    def test_rank_policy_preserves_priority_order_and_three_position_limit(self):
        p = self.portfolio('mean_rank')
        p.manager.meta.update({s:dict(p.manager.meta['A'],tradingsymbol=s) for s in ('B','C','D')})
        p.clock.at = self.now.replace(hour=10)
        p.manager.step()
        for symbol, priority in [('A',1),('B',3),('C',2),('D',.5)]:
            p.quotes.values['NSE:'+symbol] = dict(last_price=100,timestamp=p.clock.at,upper_circuit_limit=120)
            p.manager.offer(dict(symbol=symbol,direction='LONG',entry=100,stoploss=98,
                target=110,position_size=90,estimated_costs=10, true_gap=1.,
                final_score=80,stock_score=80,market_direction='BULLISH',
                details={'gap_type':'continuation'},priority_score=priority,
                offered_at=p.clock.at.timestamp()))
        for _ in range(4):
            p.manager.step();p.clock.at += timedelta(seconds=2)
        entries=[o['tradingsymbol'] for o in p.broker.book if o['order_type']=='LIMIT']
        self.assertEqual(entries,['B','C','A'])
        self.assertEqual(p.manager.snapshot()['active_count'],3)
        self.assertIn('D',p.manager.pending_candidates)
        self.assertLess(p.manager.snapshot()['capital_used'],35000)

    def test_combined_daily_loss_triggers_flatten_and_blocks_reentry(self):
        p = self.portfolio()
        # Shared executor integration: three legal positions, then a gap through
        # planned stops. The loss limit is a trigger, not a guaranteed fill cap.
        p.manager.meta.update({s:dict(p.manager.meta['A'],tradingsymbol=s) for s in ('B','C')})
        p.clock.at=self.now.replace(hour=10);p.manager.step()
        for symbol in ('A','B','C'):
            p.quotes.values['NSE:'+symbol]=dict(last_price=100,timestamp=p.clock.at)
            p.manager.offer(dict(symbol=symbol,direction='LONG',entry=100,stoploss=98,
                target=120,position_size=90,estimated_costs=10,true_gap=1,final_score=80,
                stock_score=80,market_direction='BULLISH',details={'gap_type':'continuation'},
                priority_score=3,offered_at=p.clock.at.timestamp()))
        for _ in range(4):p.manager.step()
        for symbol in ('A','B','C'):
            p.quotes.values['NSE:'+symbol]=dict(last_price=94,timestamp=p.clock.at)
        for _ in range(4):p.manager.step()
        self.assertIn('daily loss',p.manager.state['halt'])
        self.assertTrue(p.manager.state['flatten'])
        self.assertFalse(p.manager.snapshot()['entry_allowed'])

    def test_gap_in_capture_resets_hold_instead_of_crediting_missing_ticks(self):
        p = self.portfolio()
        p.frame(self.frame(volume=10000))
        p.frame(self.frame(self.now+timedelta(minutes=6),volume=10000))
        self.assertFalse(p.manager.state['trades'])
        self.assertEqual(p.engine.gap_first_seen['A'],self.now+timedelta(minutes=6))

    def test_automatic_comparison_is_isolated_bounded_and_failure_preserves_tape(self):
        import subprocess
        execution = Mock()
        execution.snapshot.return_value={'flat':True}
        tape=execution.comparison_tape
        tape.worker.is_alive.return_value=False
        tape.failed=None;tape.dropped=0;tape.path=Path(self.tmp.name)/'saved.sqlite3'
        tape.path.write_bytes(b'fixture retained')
        with patch('subprocess.run') as child:
            self.assertEqual(run_comparison_report(execution),'ok')
        self.assertEqual(child.call_args.kwargs,dict(check=True,timeout=600))
        self.assertIn(str(tape.path.resolve()),child.call_args.args[0])
        with patch('subprocess.run',side_effect=subprocess.TimeoutExpired('replay',600)):
            self.assertEqual(run_comparison_report(execution),'failed')
        self.assertEqual(tape.path.read_bytes(),b'fixture retained')
        execution.snapshot.return_value={'flat':False}
        with patch('subprocess.run') as child:
            self.assertEqual(run_comparison_report(execution),'skipped')
        child.assert_not_called()

    def test_full_replay_rescores_variants_and_keeps_control_unmodified(self):
        path=Path(self.tmp.name)/'comparison.sqlite3'
        tape=ComparisonTape(path,self.meta,lambda:self.baselines)
        for second in range(0,350,5):
            tape.queue.put(self.frame(self.now+timedelta(seconds=second),
                                     price=110 if second>=320 else 100,volume=1600))
        tape.close()
        result=run(path,engine_module)['results']
        self.assertEqual(result['mean_gated/base']['filled'],0)
        self.assertEqual(result['mean_rank/base']['filled'],0)
        for name in ('median_gated','median_rank','median_rank_rvol2'):
            self.assertEqual(result[name+'/base']['closed'],1)
            self.assertGreater(result[name+'/base']['provisional_net'],result[name+'/stress']['provisional_net'])
        self.assertEqual(self.engine.rvol_baseline,{'A':10000})

    def test_short_replay_requires_bound_and_reaches_target(self):
        self.engine.market_direction='BEARISH'
        self.engine.fo_eligible_symbols=frozenset({'A'})
        p=self.portfolio()
        for second in range(0,350,5):
            f=self.frame(self.now+timedelta(seconds=second),price=90 if second>=320 else 100,volume=10000)
            f['ticks'][0]['ohlc']['close']=102
            f['ticks'][0]['average_traded_price']=101
            p.frame(f)
        t=p.manager.state['trades']['A']
        self.assertEqual(t['sign'],-1)
        self.assertEqual(t['exit_reason'],'TARGET')
        self.assertTrue(t['closed_at'])
        self.assertGreater(p.result()['provisional_net'],0)
        self.assertLessEqual(t['orders'][0]['qty']*120,10000)


if __name__ == '__main__': unittest.main()
