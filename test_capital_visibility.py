"""Offline lifecycle regressions for reusable capital and operational visibility."""
import contextlib
import copy
import io
import json
import logging
import os
from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_auto_trading as fixtures
from execution_capital import capital_snapshot, REUSABLE, LEGACY
from auto_paper_summary import calculate, summarise
from order_manager import notify


class CapitalVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ExecutionTests()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.m = self.f.manager

    def close(self, symbol='A', price=99):
        self.f.broker.price = price
        self.m._goal(self.m.state['trades'][symbol], self.m._filled(self.m.state['trades'][symbol]), 'DEAD_TRADE')
        for _ in range(4): self.m.step()

    def test_full_exit_releases_capital_less_loss_and_cost_once(self):
        self.f.enter()
        self.close(price=99)
        self.assertEqual(self.m.snapshot()['tickets'], 2000)
        self.assertEqual(self.m.snapshot()['exposure'], 0)
        self.assertEqual(self.m.snapshot()['remaining'], 24900)  # loss 20 + costs 80
        self.assertEqual(calculate(self.m.state)['remaining'], 24900)
        self.f.restart()
        self.assertEqual(self.f.manager.snapshot()['remaining'], 24900)
        self.assertEqual(self.f.manager.state['capital_model'], REUSABLE)

    def test_new_symbol_can_enter_after_exit_despite_turnover_over_cap(self):
        self.m.daily_cap = 2100
        self.m.state['limits']['daily_cap'] = 2100
        self.f.enter()
        self.assertLess(self.m.snapshot()['remaining'], 1000)
        self.close(price=99)
        self.f.broker.price = 100
        self.m.offer(self.f.signal('B', qty=15))
        self.m.step(); self.m.step()
        self.assertEqual(self.m._filled(self.m.state['trades']['B']), 15)
        self.assertGreater(self.m.snapshot()['tickets'], 2100)
        self.assertLessEqual(self.m._budget_used(), 2100)
        self.assertFalse(self.m.state['halt'])
        self.assertEqual(self.m.snapshot()['remaining'], 420)

    def test_two_losses_do_not_block_when_monetary_risk_allows(self):
        self.f.enter(); self.close(price=99)
        self.f.broker.price = 100
        self.f.enter(symbol='B'); self.close('B', price=99)
        self.assertEqual(self.m.snapshot()['losses'], 2)
        self.assertGreater(self.m.snapshot()['remaining'], 1000)
        self.assertTrue(self.m.snapshot()['entry_allowed'])
        self.f.broker.price = 100
        self.m.offer(self.f.signal('C')); self.m.step()
        self.assertIn('C', self.m.state['trades'])

    def test_daily_1500_combined_threshold_includes_open_pnl_and_costs(self):
        self.f.enter(qty=99)
        self.assertEqual(self.m.max_daily_loss,1500)
        self.f.broker.price=86  # 99 * -14 - 80 = -1466, below threshold magnitude
        self.m.quotes=self.f.broker.quotes(['A'])
        self.m._refresh();self.m._publish()
        self.assertEqual(self.m.snapshot()['combined_net'],-1466)
        self.assertNotIn('combined daily loss limit Rs1500',self.m.snapshot()['entry_blockers'])
        self.f.broker.price=85
        self.m.step()
        self.assertTrue(self.m.state['flatten'])
        self.assertIn('Rs1500',self.m.state['halt'])
        self.assertFalse(self.m.snapshot()['entry_allowed'])
        for _ in range(4):self.m.step()
        self.assertTrue(self.m.snapshot()['flat'])
        self.f.restart()
        self.assertTrue(self.f.manager.state['flatten'])
        self.assertIn('Rs1500',self.f.manager.state['halt'])

    def test_daily_loss_exact_boundary_latches(self):
        self.f.enter(qty=20)
        self.f.broker.price=29  # -1420 gross unrealised -80 provisional = -1500
        self.m.step()
        self.assertTrue(self.m.state['flatten'])
        self.assertEqual(self.m.state['trades']['A']['exit_reason'],'DAILY_LOSS')

    def test_daily_limit_combines_multiple_open_positions(self):
        self.f.enter(qty=99);self.f.enter(symbol='B',qty=99)
        self.f.broker.price=93  # each position loses 693; total +160 costs = 1546
        self.m.step()
        self.assertTrue(self.m.state['flatten'])
        self.assertEqual(self.m.snapshot()['combined_net'],-1546)
        for _ in range(4):self.m.step()
        self.assertTrue(self.m.snapshot()['flat'])

    def test_engine_and_executor_share_1500_rule_without_streak_gate(self):
        self.f.enter();self.close(price=99)
        self.f.broker.price=100
        self.f.enter(symbol='B');self.close('B',price=99)
        self.f.broker.price=100
        e=self.f._make_signal_engine()
        # C is a fresh symbol; reuse A's fixture metadata for its signal path.
        e.all_stocks['C']=dict(e.all_stocks['A'],symbol='C')
        e.long_map['C']=e.all_stocks['C']
        for name in ('today_open','prev_close','vwap','rvol_baseline','key_levels','gap_first_seen','gap_direction'):
            getattr(e,name)['C']=copy.deepcopy(getattr(e,name)['A'])
        self.assertEqual(e._check_signal.__globals__['MAX_DAILY_LOSS_INR'],1500)
        e._check_signal('C',100,1000,self.f.now,'momentum')
        self.assertFalse(self.m.inbox.empty())
        self.assertEqual(e.risk.consecutive_losses,2)

    def test_daily_loss_blocks_candidate_whose_planned_risk_exceeds_remainder(self):
        self.f.enter(qty=20);self.close(price=90)  # realised -200, costs -80
        self.f.broker.price=100
        sig=self.f.signal('B',qty=99)
        sig.update(stoploss=88,target=130)  # 99*12 + new cost80 leaves worse than -1500
        self.m.offer(sig);self.m.step()
        self.assertNotIn('B',self.m.state['trades'])
        self.assertEqual(self.m.snapshot()['losses'],1)

    def test_short_open_loss_is_in_combined_limit(self):
        self.f.enter(qty=80,direction='SHORT')
        self.f.broker.price=118  # -1440 -80
        self.m.step()
        self.assertTrue(self.m.state['flatten'])
        self.assertIn('combined daily loss',self.m.state['halt'])

    def test_stale_quote_is_not_reported_as_known_combined_pnl(self):
        self.f.enter();self.f.broker.stale=True;self.m.step()
        self.assertIsNone(self.m.snapshot()['combined_net'])

    def test_exit_request_or_cancel_pending_does_not_release_funds(self):
        self.f.enter()
        before = self.m.snapshot()['remaining']
        self.f.broker.cancel_pending = True
        self.m._goal(self.m.state['trades']['A'], 20, 'REVERSAL')
        self.m.step(); self.m.step()
        self.assertEqual(self.m.snapshot()['remaining'], before)
        self.assertEqual(self.m._exited(self.m.state['trades']['A']), 0)

    def test_partial_exit_releases_only_confirmed_quantity(self):
        self.f.enter()
        self.f.broker.exit_fill = 5
        self.m._goal(self.m.state['trades']['A'], 10, 'PARTIAL_1R')
        self.m.step()  # cancel protective stop, no fills released
        self.assertEqual(self.m.snapshot()['remaining'], 22920)
        self.m.step()  # 5 confirmed exit fills, goal still 10
        self.assertEqual(self.m.snapshot()['exposure'], 1500)
        self.assertEqual(self.m.snapshot()['remaining'], 23420)
        self.assertEqual(self.m.snapshot()['tickets'], 2000)

    def test_pending_and_unknown_entry_reserve_bound_and_cost(self):
        self.f.broker.throw_before = True
        self.m.offer(self.f.signal(direction='SHORT'))
        self.m.step()
        self.assertEqual(self.m.snapshot()['remaining'], 25000-20*120-80)
        self.m.step()
        self.assertEqual(self.m.snapshot()['remaining'], 22520)
        self.assertFalse(self.m.snapshot()['entry_allowed'])
        self.assertEqual(len(self.f.broker.actions), 1)

    def test_short_exit_sign_and_profit_does_not_expand_ceiling(self):
        self.f.enter(direction='SHORT')
        self.close(price=90)
        self.assertEqual(self.m.snapshot()['gross'], 200)
        self.assertEqual(self.m.snapshot()['net'], 120)
        self.assertEqual(self.m.snapshot()['remaining'], 25000)
        self.assertEqual(calculate(self.m.state)['remaining'], 25000)

    def test_same_day_upgrade_preserves_legacy_next_day_switches(self):
        self.f.enter(); self.close(price=99)
        self.m.state.pop('capital_model')
        self.m.journal.save()
        self.f.restart()
        self.assertEqual(self.f.manager.state['capital_model'], LEGACY)
        self.assertEqual(self.f.manager.snapshot()['remaining'], 22920)
        previous_day = self.f.now.date().isoformat()
        self.f.now += timedelta(days=1)
        self.f.broker.book.clear()  # broker day book resets independently
        self.f.restart()
        self.assertEqual(self.f.manager.state['capital_model'], REUSABLE)
        archived = json.loads(self.f.journal.db.execute('SELECT body FROM history WHERE day=?', (previous_day,)).fetchone()[0])
        self.assertEqual(archived['capital_model'], LEGACY)
        self.assertEqual(self.f.manager.snapshot()['remaining'], 25000)

    def test_open_legacy_position_is_restored_without_switching_model(self):
        self.f.enter()
        self.m.state.pop('capital_model');self.m.journal.save()
        self.f.restart()
        self.assertEqual(self.f.manager.state['capital_model'],LEGACY)
        self.assertEqual(self.f.manager.snapshot()['exposure'],2000)
        self.assertEqual(len(self.f.broker.book),2)

    def test_partial_pending_entry_reserves_remainder_without_double_cost(self):
        self.m.offer(self.f.signal());self.m.step()
        self.f.broker.book[0].update(status='OPEN',filled_quantity=7,average_price=100)
        self.f.broker.cancel_pending=True
        self.m.step()
        t=self.m.state['trades']['A']
        expected=25000-700-13*t['reservation_price']-80
        self.assertAlmostEqual(self.m.snapshot()['remaining'],expected)
        self.assertFalse(self.m.snapshot()['entry_allowed'])
        self.assertEqual(calculate(self.m.state)['allowances'],80)

    def test_user_snapshot_reconstructs_reusable_headroom(self):
        state = {'capital_model': REUSABLE, 'trades': {}}
        for s, qty, entry, exit_price in [('ABDL',13,740,728.25),('SEDEMAC',2,3463.30,3293.90),
                                           ('SHANTIGOLD',33,296.01,None),('STYL',21,386.30,None)]:
            orders = [dict(kind='ENTRY',qty=qty,filled=qty,average=entry,status='COMPLETE')]
            if exit_price: orders.append(dict(kind='EXIT',qty=qty,filled=qty,average=exit_price,status='COMPLETE'))
            state['trades'][s] = dict(orders=orders,sign=1)
        c = capital_snapshot(state,35000,80)
        self.assertEqual(float(c['tickets']),34427.23)
        self.assertEqual(float(c['exposure']),17880.63)
        self.assertEqual(float(c['remaining']),16307.82)

    def test_console_entry_fill_and_close_once_independent_of_telegram(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN':'test', 'TELEGRAM_CHAT_ID':'test'}):
            with self.assertLogs('order_manager', logging.INFO) as logs:
                self.f.enter(); self.close(price=99)
                self.m.step(); self.f.restart()
        messages = '\n'.join(logs.output)
        self.assertEqual(messages.count('A ENTRY FILLED'),1)
        self.assertEqual(messages.count('A CLOSED (DEAD_TRADE)'),1)
        self.assertIn('exited 20sh @ Rs99.00',messages)
        self.assertIn('provisional net Rs-100.00',messages)

    def test_telegram_failure_does_not_remove_console_event(self):
        response = Mock()
        response.raise_for_status.side_effect = RuntimeError('delivery failed')
        self.m._deliver_alert = notify
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN':'test', 'TELEGRAM_CHAT_ID':'test'}), \
             patch.dict('sys.modules', {'requests':SimpleNamespace(post=Mock(return_value=response))}):
            with self.assertLogs('order_manager', logging.INFO) as logs:
                self.m.alerts('test event'); self.m.alerts('test event')
        self.assertEqual(sum('test event' in x for x in logs.output),1)
        self.assertTrue(any('delivery failed' in x for x in logs.output))

    def test_stop_fill_wins_over_previously_requested_reversal_label(self):
        self.f.enter()
        self.m._goal(self.m.state['trades']['A'],20,'REVERSAL')
        self.f.broker.cancel_fills = True
        self.m.step(); self.m.step()
        t = self.m.state['trades']['A']
        self.assertEqual(t['exit_reason'],'REVERSAL')  # intent retained
        self.assertEqual(t['closed_reason'],'STOPLOSS')  # actual protective fill
        self.assertEqual(calculate(self.m.state)['rows'][0]['status'],'STOPLOSS')

    def test_stale_open_quotes_visible_and_block_new_entries(self):
        self.f.enter()
        self.f.broker.stale = True
        self.m.step()
        v = self.m.snapshot()
        self.assertIn('open-position quotes stale/unavailable',v['entry_blockers'])
        self.assertFalse(v['quote_fresh']['A'])
        self.assertIsNotNone(v['cycle_success_at'])  # successful reconciliation != fresh quotes
        self.assertFalse(v['entry_allowed'])

    def test_summary_distinguishes_turnover_capital_and_entry_status(self):
        self.f.enter(); self.close(price=99)
        out = io.StringIO()
        with contextlib.redirect_stdout(out): summarise(self.f.path)
        self.assertIn('Cumulative entry turnover (history):',out.getvalue())
        self.assertIn('Available capital: ₹24,900.00',out.getvalue())
        self.assertIn('recorded snapshot, not a live health check',out.getvalue())

    def test_capital_after_close_reaches_signal_sizing(self):
        self.m.daily_cap=11000
        self.m.state['limits']['daily_cap']=11000
        self.f.enter(symbol='B',qty=99)
        self.close('B',price=99)
        self.f.broker.price=100
        e=self.f._make_signal_engine()
        e._check_signal('A',100,1000,self.f.now,'momentum')
        self.assertFalse(self.m.inbox.empty())
        self.m.step();self.m.step()
        self.assertGreater(self.m._filled(self.m.state['trades']['A']),0)
        self.assertGreater(self.m.snapshot()['tickets'],11000)

    def test_post_exit_notified_without_symbol_tick_then_scan_can_run(self):
        e=self.f._make_signal_engine()
        self.f.enter();self.close(price=99)
        with self.assertLogs('integration',logging.INFO) as logs:
            e._poll_closed_trades();e._poll_closed_trades()
        self.assertTrue(e._rescan_requested.is_set())
        self.assertEqual(len(logs.output),1)
        self.assertEqual(e._scan_reason(self.f.now,{'morning','10 AM'},100,131),'post-exit/retry')

    def test_periodic_scan_window_pacing_and_coalescing(self):
        e=self.f._make_signal_engine()
        done={'morning','10 AM'}
        self.assertIsNone(e._scan_reason(self.f.now,done,100,399))
        self.assertEqual(e._scan_reason(self.f.now,done,100,400),'periodic')
        self.assertIsNone(e._scan_reason(self.f.now.replace(hour=15),done,100,500))
        self.assertIsNone(e._scan_reason(self.f.now.replace(hour=9,minute=34),done,100,500))
        e._rescan_requested.set()
        self.assertIsNone(e._scan_reason(self.f.now,done,100,129))
        self.assertEqual(e._scan_reason(self.f.now,done,100,130),'post-exit/retry')

    def test_maintenance_dispatches_periodic_scan_without_exit(self):
        e=self.f._make_signal_engine()
        ns=e._check_signal.__globals__
        now=self.f.now
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return now
        ns.update(datetime=Clock,time=SimpleNamespace(monotonic=Mock(side_effect=[100,100,131,131,432,432])))
        e._scan_market=Mock(return_value=0)
        e._scan_stop=Mock()
        e._scan_stop.is_set.side_effect=[False,False,False,True]
        e._maintenance_loop()
        self.assertEqual([c.args[0] for c in e._scan_market.call_args_list],['morning','10 AM','periodic'])

    def test_loss_block_defers_discovery_and_explains_why(self):
        e=self.f._make_signal_engine()
        self.m.view['entry_blockers']=['combined daily loss limit Rs1500']
        self.m.view['entry_allowed']=False
        now=self.f.now
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return now
        e._check_signal.__globals__.update(datetime=Clock,get_dynamic_gappers=Mock(side_effect=AssertionError('must not scan')))
        with self.assertLogs('integration',logging.INFO) as logs:
            self.assertIsNone(e._scan_market('post-exit'))
            self.assertIsNone(e._scan_market('post-exit'))
        self.assertEqual(len(logs.output),1)
        self.assertIn('daily loss',logs.output[0])

    def test_pre_entry_window_still_observes_hold_without_submitting(self):
        e=self.f._make_signal_engine()
        self.f.now=self.f.now.replace(hour=9,minute=31)
        self.m.step()
        e.gap_first_seen.clear()
        self.assertFalse(self.m.snapshot()['entry_allowed'])
        e._check_signal('A',100,1000,self.f.now,'momentum')
        self.assertIn('A',e.gap_first_seen)
        self.assertTrue(self.m.inbox.empty())

    def test_candidate_log_once_but_executor_rejection_allows_retry(self):
        e=self.f._make_signal_engine()
        with self.assertLogs('integration',logging.INFO) as logs:
            e._check_signal('A',100,1000,self.f.now,'momentum')
            self.f.broker.stale=True;self.m.step()  # candidate rejected
            self.f.broker.stale=False
            self.f.now+=timedelta(seconds=6)
            e._check_signal('A',100,1000,self.f.now,'momentum')
        self.assertEqual(sum('candidate queued' in s for s in logs.output),1)
        self.assertNotIn('A',e.traded_today)
        self.assertNotIn('A',e.signals)
        self.m.step();self.m.step()
        self.assertEqual(self.m._filled(self.m.state['trades']['A']),99)

    def test_ofss_route_no_info_spam_when_profit_screen_fails(self):
        e=self.f._make_signal_engine()
        e.today_open['A']=98;e.prev_close['A']=100;e.vwap['A']=99
        e.gap_direction['A']='SHORT';e.fo_eligible_symbols=frozenset({'A'})
        e._compute_opportunity_score=lambda *args:(90,{'gap_type':'continuation','rvol':10})
        e.key_levels['A']['atr14']=.1  # direction qualifies, profit screen fails
        with patch.object(e._check_signal.__globals__['logger'],'info') as log:
            for _ in range(20): e._check_signal('A',98,1000,self.f.now,'momentum')
        self.assertFalse(any('SHORT' in str(c) for c in log.call_args_list))
        self.assertTrue(self.m.inbox.empty())

    def test_heartbeat_does_not_confuse_rest_scan_with_websocket(self):
        e=self.f._make_signal_engine()
        ns=e._check_signal.__globals__
        now=self.f.now
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None): return now
        ns['datetime']=Clock
        e._process_scan_quotes({'NSE:A':dict(last_price=100,timestamp=now,volume=1000,
                                           average_traded_price=99,ohlc={'open':100,'close':98})})
        with self.assertLogs('integration',logging.INFO) as logs:
            e._log_heartbeat();e._log_heartbeat()
        self.assertEqual(len(logs.output),1)
        self.assertIn('WS tick age=never',logs.output[0])
        now+=timedelta(seconds=61)
        with self.assertLogs('integration',logging.INFO) as logs: e._log_heartbeat()
        self.assertIn('executor cycle age=61s',logs.output[0])


if __name__ == '__main__': unittest.main()
