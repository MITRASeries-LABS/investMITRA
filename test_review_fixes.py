"""Offline regression coverage for the September 24 review. No credentials or I/O services."""
import ast
import copy
from datetime import date, datetime, timedelta
import io
import logging
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock, patch
import contextlib

import test_auto_trading as fixtures
from order_manager import IST, AutoOrderManager
from signal_runtime import RateLimitedKite, previous_session, freshness_errors, load_nse_holidays
from auto_paper_summary import calculate, report_limits


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ExecutionTests()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.engine = self.f._make_signal_engine()
        owner = self

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return owner.f.now

        self.ns = self.engine._check_signal.__globals__
        self.ns.update(datetime=Clock, time=time)
        tree = ast.parse((Path(__file__).parent / 'scripts/intraday_signals.py').read_text(encoding='utf-8'))
        funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name in {'classify_gap', 'load_fo_eligible_symbols', 'preflight_check'}]
        exec(compile(ast.Module(body=funcs, type_ignores=[]), 'review-fixtures', 'exec'), self.ns)
        self.ns['SECTOR_INDEX_MAP'] = {}

    def quote(self, volume=1000, **changes):
        q = dict(last_price=100, volume=volume, timestamp=self.f.now,
                 average_traded_price=99, ohlc={'open': 100, 'close': 98})
        q.update(changes)
        return q

    def add_b(self):
        e = self.engine
        stock = dict(e.all_stocks['A'], symbol='B')
        e.all_stocks['B'] = e.long_map['B'] = stock
        e.token_map['B'] = 2
        e.rev_tokens[2] = 'B'
        e.rvol_baseline['B'] = 1000
        e.key_levels['B'] = {'atr14': 2}
        e.gap_first_seen['B'] = self.f.now - timedelta(minutes=6)
        e.gap_direction['B'] = 'LONG'

    def test_scan_preserves_each_symbols_volume_and_reports_queue_count(self):
        self.add_b()
        seen = {}
        def score(symbol, ltp, volume, gap, session):
            seen[symbol] = volume
            return 90, {'gap_type': 'continuation', 'rvol': 8}
        self.engine._compute_opportunity_score = score
        queued = self.engine._process_scan_quotes({'NSE:A': self.quote(1000), 'NSE:B': self.quote(2000)})
        self.assertEqual(seen, {'A': 1000, 'B': 2000})
        self.assertEqual(queued, 2)
        self.assertEqual(self.f.manager.inbox.qsize(), 2)
        self.assertFalse(self.engine.signals)

    def test_real_scoring_rescan_reaches_fill_and_target_exit(self):
        del self.engine._compute_opportunity_score
        self.assertEqual(self.engine._process_scan_quotes({'NSE:A':self.quote(1000)}),1)
        self.f.manager.step();self.f.manager.step()
        t=self.f.manager.state['trades']['A']
        self.assertEqual(t['orders'][0]['filled'],99)
        self.assertEqual(t['signal']['details']['rvol'],8.33)
        self.assertEqual(t['target'],106)
        self.f.broker.price=106
        for _ in range(4):self.f.manager.step()
        self.assertTrue(self.f.manager.snapshot()['flat'])
        self.assertEqual(t['exit_reason'],'TARGET')

    def test_scan_waits_for_real_five_minute_hold(self):
        e = self.engine
        e.gap_first_seen.clear()
        self.assertEqual(e._process_scan_quotes({'NSE:A': self.quote()}), 0)
        started = e.gap_first_seen['A']
        for _ in range(4):
            self.f.now += timedelta(minutes=1)
            self.assertEqual(e._process_scan_quotes({'NSE:A': self.quote()}), 0)
        self.assertEqual(e.gap_first_seen['A'], started)
        self.f.now += timedelta(minutes=1)
        self.assertEqual(e._process_scan_quotes({'NSE:A': self.quote()}), 1)

    def test_gap_fill_resets_hold_even_while_executor_not_ready(self):
        self.f.manager.view['ready'] = False
        self.engine._process_scan_quotes({'NSE:A': self.quote(last_price=98)})
        self.assertNotIn('A', self.engine.gap_first_seen)
        self.f.manager.view['ready'] = True
        self.assertEqual(self.engine._process_scan_quotes({'NSE:A': self.quote()}), 0)
        self.assertEqual(self.engine.gap_first_seen['A'], self.f.now)

    def test_missing_ticks_invalidate_continuous_hold(self):
        self.engine._last_tick_at['A'] = self.f.now - timedelta(minutes=2)
        self.assertEqual(self.engine._process_scan_quotes({'NSE:A': self.quote()}), 0)
        self.assertEqual(self.engine.gap_first_seen['A'], self.f.now)

    def test_stale_scan_quote_cannot_seed_hold(self):
        self.engine.gap_first_seen.clear()
        self.engine._process_scan_quotes({'NSE:A': self.quote(timestamp=self.f.now-timedelta(seconds=30))})
        self.assertFalse(self.engine.gap_first_seen)

    def test_lunch_uses_actual_session_even_if_caller_says_momentum(self):
        self.f.now = self.f.now.replace(hour=12)
        seen = []
        self.engine._compute_opportunity_score = lambda *args: (seen.append(args[-1]) or 90, {'gap_type':'continuation', 'rvol':5})
        self.engine._check_signal('A', 100, 2200, self.f.now, 'momentum')  # RVOL 5x at noon
        self.assertEqual(seen, ['choppy'])
        self.assertTrue(self.f.manager.inbox.empty())

    def test_lunch_priority_uses_actual_blended_score(self):
        self.f.now = self.f.now.replace(hour=12)
        e = self.engine
        e.prev_close['A'] = 100 / 1.007
        e._compute_opportunity_score = lambda *args: (90, {'gap_type':'continuation', 'rvol':8})
        e._check_signal('A', 100, 3600, self.f.now, 'momentum')
        sig = self.f.manager.inbox.get_nowait()
        self.assertEqual(sig['session'], 'choppy')
        self.assertGreaterEqual(sig['priority_score'], 5)

    def test_afternoon_does_not_use_momentum_gap_threshold(self):
        self.f.now = self.f.now.replace(hour=14)
        self.engine.prev_close['A'] = 100/1.0035
        self.engine._check_signal('A', 100, 10000, self.f.now, 'momentum')
        self.assertTrue(self.f.manager.inbox.empty())

    def test_no_entries_before_935_or_after_1500(self):
        for hour, minute in ((9, 34), (15, 0)):
            self.f.now = self.f.now.replace(hour=hour, minute=minute)
            self.engine._check_signal('A', 100, 10000, self.f.now, 'momentum')
            self.f.manager.offer(self.f.signal())
            self.f.manager.step()
            self.assertFalse(self.f.broker.book)

    def test_closed_trade_detection_needs_no_websocket_tick(self):
        self.f.manager.view['trades'] = {'A': {'closed_at':'now', 'orders':[{'filled':10}]}}
        self.engine._poll_closed_trades()
        self.assertTrue(self.engine._rescan_requested.is_set())
        self.engine._rescan_requested.clear()
        self.engine._poll_closed_trades()
        self.assertFalse(self.engine._rescan_requested.is_set())

    def test_simultaneous_close_notifications_coalesce_without_threads(self):
        with patch('threading.Thread', side_effect=AssertionError('No per-exit thread')):
            self.engine._trigger_post_exit_scan('A')
            self.engine._trigger_post_exit_scan('B')
        self.assertTrue(self.engine._rescan_requested.is_set())

    def test_concurrent_scan_is_coalesced(self):
        self.engine._scan_lock.acquire()
        try:
            self.assertEqual(self.engine._scan_market('post-exit'), 0)
            self.assertTrue(self.engine._rescan_requested.is_set())
        finally:
            self.engine._scan_lock.release()

    def test_scan_discovers_subscribes_and_initializes_new_symbol(self):
        e = self.engine
        e.instrument_tokens = {'A':1, 'B':2}
        e.ticker = Mock(MODE_FULL='full')
        e.kite = SimpleNamespace(quote=lambda symbols: {symbol:self.quote() for symbol in symbols})
        self.ns.update(get_dynamic_gappers=lambda *a: [dict(e.all_stocks['A'], symbol='B')],
                       get_key_levels=lambda symbols: {'B':{'atr14':2}},
                       get_rvol_baseline=lambda: {'A':1000,'B':1000})
        e._scan_market('10 AM')
        e.ticker.subscribe.assert_called_once_with([2])
        self.assertEqual(e.rev_tokens[2], 'B')
        self.assertEqual(e.gap_first_seen['B'], self.f.now)
        self.assertNotIn('B', e.execution_offers)

    def test_missing_previous_close_or_atr_cannot_be_synthesized(self):
        e = self.engine
        e.prev_close.clear()
        e._check_signal('A', 100, 1000, self.f.now, 'momentum')
        self.assertTrue(self.f.manager.inbox.empty())
        e.prev_close['A'] = 98
        e.key_levels.clear()
        e._check_signal('A', 100, 1000, self.f.now, 'momentum')
        self.assertTrue(self.f.manager.inbox.empty())

    def test_dynamic_tier_cannot_bypass_rvol_floor(self):
        self.engine.all_stocks['A']['tier'] = 2
        self.engine._check_signal('A',100,480,self.f.now,'momentum')  # actual RVOL=4
        self.assertTrue(self.f.manager.inbox.empty())

    def configure_short(self, score=65):
        e = self.engine
        e.all_stocks['A']['investmitra_score'] = score
        e.prev_close['A'] = 102
        e.vwap['A'] = 101
        e.gap_direction['A'] = 'SHORT'
        e.fo_eligible_symbols = frozenset({'A'})

    def test_neutral_quality_short_queues_at_65(self):
        self.configure_short()
        self.engine._check_signal('A',100,1000,self.f.now,'momentum')
        self.assertEqual(self.f.manager.inbox.get_nowait()['direction'], 'SHORT')

    def test_neutral_quality_short_rejects_score_64(self):
        self.configure_short(64)
        self.engine._check_signal('A',100,1000,self.f.now,'momentum')
        self.assertTrue(self.f.manager.inbox.empty())

    def test_missing_fo_membership_blocks_short(self):
        self.configure_short()
        self.engine.fo_eligible_symbols = frozenset()
        self.engine._check_signal('A',100,1000,self.f.now,'momentum')
        self.assertTrue(self.f.manager.inbox.empty())

    def test_neutral_short_floor_covers_dedicated_and_override_routes(self):
        for score, override in ((40, False), (64, True)):
            with self.subTest(score=score, override=override):
                self.configure_short(score)
                e = self.engine
                stock = e.all_stocks['A']
                stock['direction_override'] = 'SHORT' if override else None
                e.long_map = {}
                e.short_map = {'A': stock}
                e._check_signal('A', 100, 1000, self.f.now, 'momentum')
                self.assertTrue(self.f.manager.inbox.empty())
                self.assertIn('stock score >=65', e.signal_rejections['A'][0])

    def test_bearish_dedicated_short_route_is_preserved(self):
        self.configure_short(40)
        e = self.engine
        e.market_direction = 'BEARISH'
        e.short_map = {'A': e.all_stocks['A']}
        e.long_map = {}
        e._check_signal('A', 100, 1000, self.f.now, 'momentum')
        sig = self.f.manager.inbox.get_nowait()
        self.assertEqual(sig['direction'], 'SHORT')
        self.assertEqual(sig['market_direction'], 'BEARISH')

    def test_executor_blocks_neutral_short_bypass_and_missing_metadata(self):
        for changes in ({'stock_score': 40}, {'stock_score': 64},
                        {'stock_score': float('nan')}, {'stock_score': None},
                        {'market_direction': None}):
            with self.subTest(changes=changes):
                sig = self.f.signal(direction='SHORT')
                sig.update(changes)
                self.f.manager.quotes = self.f.broker.quotes(['A'])
                self.f.manager._accept(sig)
                self.assertEqual(self.f.broker.book, [])
        sig = self.f.signal(direction='SHORT')
        del sig['market_direction']
        self.f.manager.offer(sig)
        self.f.manager.step()
        self.assertEqual(self.f.broker.book, [])

    def test_executor_accepts_neutral_short_score_65(self):
        sig = self.f.signal(direction='SHORT')
        sig['stock_score'] = 65
        self.f.manager.offer(sig)
        self.f.manager.step()
        self.assertEqual(self.f.broker.book[0]['transaction_type'], 'SELL')

    def test_signal_box_uses_resized_ticket_and_is_not_duplicated_on_restart(self):
        sig = self.f.signal(qty=250)
        sig.update(cap='SMALL', session='momentum')
        sig['details']['rvol'] = 8.5
        self.f.manager.offer(sig)
        self.f.manager.step()
        messages = [m for m in self.f.alerts if ' SIGNAL - ' in m]
        self.assertEqual(len(messages), 1)
        message = messages[0]
        qty = self.f.broker.book[0]['quantity']
        self.assertLess(qty, 250)
        for fragment in ('LONG SIGNAL - A [SMALL]', 'ENTRY SUBMITTED; awaiting fill',
                         f'Qty: {qty} shares', 'Entry limit: Rs100.10',
                         'Target: Rs120.00', 'Stop: Rs98.00',
                         'RVOL: 8.5x', 'Blended score: 75.00'):
            self.assertIn(fragment, message)
        self.assertEqual(sum('ENTRY SUBMITTED' in m for m in self.f.alerts), 1)
        self.assertNotIn('Open Kite', message)
        self.f.manager.step()
        self.f.restart()
        self.assertEqual(sum(' SIGNAL - ' in m for m in self.f.alerts), 1)

    def test_rejected_signal_does_not_send_signal_box(self):
        sig = self.f.signal()
        sig['final_score'] = 50
        self.f.manager.offer(sig)
        self.f.manager.step()
        self.assertFalse(any(' SIGNAL - ' in m for m in self.f.alerts))

    def test_unknown_entry_signal_box_does_not_claim_submission_or_fill(self):
        self.f.broker.throw_after = True
        self.f.manager.offer(self.f.signal())
        self.f.manager.step()
        messages = [m for m in self.f.alerts if ' SIGNAL - ' in m]
        self.assertEqual(len(messages), 1)
        self.assertIn('ENTRY STATUS UNKNOWN; awaiting reconciliation', messages[0])
        self.assertNotIn('ENTRY SUBMITTED', messages[0])
        self.assertNotIn('FILLED', messages[0])

    def test_failed_fo_lookup_does_not_allow_shorts(self):
        self.ns.update(psycopg2=SimpleNamespace(connect=Mock(side_effect=ConnectionError())), NEON_URL='unused')
        self.assertEqual(self.ns['load_fo_eligible_symbols'](), frozenset())

    def test_real_opportunity_scoring_is_direction_symmetric_without_database(self):
        e = self.engine
        del e._compute_opportunity_score
        self.ns['psycopg2'] = SimpleNamespace(connect=Mock(side_effect=AssertionError('DB in tick callback')))
        e.market_direction='BULLISH'
        e.breadth={'NIFTY 50':{'pct_change':.5,'advances':35,'declines':15}}
        e.sector_quotes={'NSE:NIFTY 50':1}
        e.sentiment={'A':.5}
        long_score, long_details = e._compute_opportunity_score('A',100,1000,2,'momentum')
        e.market_direction='BEARISH'
        e.vwap['A']=101
        e.breadth={'NIFTY 50':{'pct_change':-.5,'advances':15,'declines':35}}
        e.sector_quotes={'NSE:NIFTY 50':-1}
        e.sentiment={'A':-.5}
        short_score, short_details = e._compute_opportunity_score('A',100,1000,-2,'momentum')
        self.assertEqual(long_score, short_score)
        self.assertEqual(long_details['vwap_score'],short_details['vwap_score'])
        self.ns['psycopg2'].connect.assert_not_called()

    def test_ticket_cap_uses_actual_order_bound(self):
        self.f.manager.daily_cap = 35000
        self.f.broker.price = 100.2
        self.f.enter(qty=100)
        self.assertLessEqual(self.f.manager.snapshot()['tickets'],10000)
        self.assertEqual(self.f.manager.state['trades']['A']['orders'][0]['qty'],99)

    def test_short_ticket_cap_reserves_upper_circuit(self):
        self.f.enter(qty=100,direction='SHORT')
        t=self.f.manager.state['trades']['A']
        self.assertLessEqual(t['reservation_price']*t['orders'][0]['qty'],10000)

    def test_executor_rechecks_profit_screen_after_resizing(self):
        s=self.f.signal(qty=100)
        s.update(minimum_net_screen=995, profit_screen_fraction=.5, estimated_costs=0)
        self.f.manager.offer(s)
        self.f.manager.step()
        self.assertFalse(self.f.broker.book)

    def test_long_target_closes_remainder_after_partial_and_restart(self):
        s=self.f.signal()
        s['target']=104
        self.f.manager.offer(s)
        self.f.manager.step();self.f.manager.step()
        self.f.broker.price=102
        for _ in range(4):self.f.manager.step()
        self.assertEqual(self.f.manager._remaining(self.f.manager.state['trades']['A']),10)
        self.f.restart()
        self.f.broker.price=104
        for _ in range(4):self.f.manager.step()
        t=self.f.manager.state['trades']['A']
        self.assertEqual(t['exit_reason'],'TARGET')
        self.assertTrue(self.f.manager.snapshot()['flat'])
        self.assertEqual(self.f.manager.snapshot()['gross'],60)  # 1.5R, not 2R

    def test_short_jump_to_target_closes_full_quantity_once(self):
        s=self.f.signal(direction='SHORT');s['target']=96
        self.f.manager.offer(s)
        self.f.manager.step();self.f.manager.step()
        self.f.broker.price=96
        for _ in range(4):self.f.manager.step()
        t=self.f.manager.state['trades']['A']
        self.assertEqual(t['exit_reason'],'TARGET')
        self.assertEqual(self.f.manager._exited(t),20)
        self.assertTrue(self.f.manager.snapshot()['flat'])

    def test_short_reversal_is_direction_aware(self):
        self.f.enter(direction='SHORT')
        self.f.broker.price=100.6
        self.f.manager.step()
        self.f.now += timedelta(minutes=10)
        for _ in range(4):self.f.manager.step()
        self.assertEqual(self.f.manager.state['trades']['A']['exit_reason'],'REVERSAL')
        self.assertTrue(self.f.manager.snapshot()['flat'])

    def test_legacy_closed_tickets_remain_spent_under_35000_cap(self):
        self.f.manager.state['capital_model']='cumulative_tickets_v1'
        self.f.manager.daily_cap=35000
        self.f.enter(qty=100)
        used=self.f.manager._budget_used()
        self.f.manager.request_flatten()
        for _ in range(4):self.f.manager.step()
        self.assertEqual(self.f.manager._budget_used(),used)
        self.assertEqual(self.f.manager.snapshot()['remaining'],35000-used)

    def test_summary_uses_persisted_execution_limits(self):
        self.f.enter()
        self.f.manager.state['limits']['daily_cap']=35000
        result=calculate(self.f.manager.state)
        self.assertEqual(result['remaining'],35000-result['budget_used'])
        self.assertEqual(report_limits(self.f.manager.state,25000,90),(25000,90))

    def run_preflight(self, score_date, price_date, *, calendar_fails=False):
        index_connection=MagicMock()
        index_connection.cursor.return_value.fetchone.return_value=(1,)
        connection=MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchone.side_effect=[
            (score_date,), (price_date,200000), (1,)]
        self.ns.update(API_KEY='test', ACCESS_TOKEN='test', NEON_URL='unused',
                       os=SimpleNamespace(getenv=lambda name:'test'),
                       psycopg2=SimpleNamespace(connect=Mock(side_effect=[index_connection,connection])),
                       load_nse_holidays=Mock(side_effect=ValueError('unavailable')) if calendar_fails else lambda _: {date(2026,1,26)},
                       previous_session=previous_session, freshness_errors=freshness_errors,
                       timedelta=timedelta)
        with patch.dict('sys.modules',{'kiteconnect':SimpleNamespace(KiteConnect=object,KiteTicker=object)}), contextlib.redirect_stdout(io.StringIO()):
            return self.ns['preflight_check']()

    def test_preflight_blocks_stale_price_snapshot(self):
        self.assertFalse(self.run_preflight(date(2026,9,9),date(2026,9,8)))

    def test_preflight_accepts_complete_previous_session(self):
        self.assertTrue(self.run_preflight(date(2026,9,9),date(2026,9,9)))

    def test_preflight_blocks_unknown_calendar(self):
        self.assertFalse(self.run_preflight(date(2026,9,9),date(2026,9,9),calendar_fails=True))


class CalendarAndQuoteTests(unittest.TestCase):
    def test_prior_session_skips_weekend_and_exchange_holiday(self):
        holidays={date(2026,9,14)}
        self.assertEqual(previous_session(date(2026,9,15),holidays),date(2026,9,11))

    def test_incomplete_calendar_and_nontrading_day_fail_closed(self):
        for today, holidays in ((date(2026,9,24),set()),
                                (date(2026,9,14),{date(2026,9,14)}),
                                (date(2026,9,26),{date(2026,9,14)})):
            with self.assertRaises(ValueError):previous_session(today,holidays)

    def test_stale_prices_or_scores_cannot_pass_on_large_row_count(self):
        today=date(2026,9,24);expected=date(2026,9,23)
        self.assertFalse(freshness_errors(today,expected,expected,expected,1,200000))
        self.assertTrue(freshness_errors(today,expected,expected,date(2026,9,21),1,200000))
        self.assertTrue(freshness_errors(today,expected,date(2026,9,21),expected,1,200000))
        self.assertTrue(freshness_errors(today,expected,expected,expected,0,200000))

    def test_official_calendar_parser_uses_cash_market_segment(self):
        session=Mock()
        session.get.return_value.json.return_value={'CM':[{'tradingDate':'14-Sep-2026'}]}
        context=Mock()
        context.__enter__=Mock(return_value=session)
        context.__exit__=Mock(return_value=False)
        with patch.dict('sys.modules',{'requests':SimpleNamespace(Session=Mock(return_value=context))}):
            self.assertEqual(load_nse_holidays(date(2026,9,24)),{date(2026,9,14)})

    def test_quote_requests_share_pacing_across_threads(self):
        times=[]
        client=RateLimitedKite(SimpleNamespace(quote=lambda *a: times.append(time.monotonic()) or {}),interval=.04)
        threads=[threading.Thread(target=client.quote,args=(['NSE:A'],)),
                 threading.Thread(target=client.execution_quote,args=(['NSE:B'],)),
                 threading.Thread(target=client.quote,args=(['NSE:C'],))]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=2)
        self.assertEqual(len(times),3)
        self.assertTrue(all(b-a >= .035 for a,b in zip(times,times[1:])))

    def test_slow_quote_cannot_overlap_or_bunch_next_request(self):
        entered = threading.Event()
        release = threading.Event()
        second = threading.Event()
        timestamps = {}
        def quote(symbols):
            if symbols == ['NSE:A']:
                entered.set()
                release.wait(timeout=2)
                timestamps['first_end'] = time.monotonic()
            else:
                timestamps['second_start'] = time.monotonic()
                second.set()
            return {}
        client = RateLimitedKite(SimpleNamespace(quote=quote), interval=.04)
        first = threading.Thread(target=client.quote, args=(['NSE:A'],))
        next_call = threading.Thread(target=client.execution_quote, args=(['NSE:B'],))
        first.start()
        try:
            self.assertTrue(entered.wait(timeout=2))
            next_call.start()
            self.assertFalse(second.wait(timeout=.12))
        finally:
            release.set()
            first.join(timeout=2)
            if next_call.ident is not None:
                next_call.join(timeout=2)
        self.assertTrue(second.is_set())
        self.assertGreaterEqual(timestamps['second_start'] - timestamps['first_end'], .035)

    def test_failed_quote_releases_pacing_slot(self):
        underlying = Mock(side_effect=[ConnectionError('quote unavailable'), {}])
        client = RateLimitedKite(SimpleNamespace(quote=underlying), interval=0)
        with self.assertRaises(ConnectionError):
            client.quote(['NSE:A'])
        self.assertFalse(client._quote_in_flight)
        self.assertEqual(client.execution_quote(['NSE:B']), {})


if __name__=='__main__':
    unittest.main()
