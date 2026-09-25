"""Offline checks for forward-only data integrity and trading isolation."""
import contextlib
import copy
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import Mock, patch

import test_auto_trading as fixtures  # adds scripts to sys.path; no production imports
from shadow_validation import StudyStore, ShadowObserver, IST, SPEC, VERSION, filters, capture_features
from shadow_validation_report import compare, summarise, read_report


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'shadow.sqlite3'
        self.store = StudyStore(self.path)
        self.addCleanup(lambda: self.store.close() if self.store else None)
        self.now = datetime(2026, 9, 25, 10, 0, tzinfo=IST)

    def features(self, **overrides):
        f = dict(day=self.now.date().isoformat(), observed_at=self.now.timestamp(),
                 price_at=self.now.timestamp(), strategy_id='frozen', symbol='A', direction='LONG',
                 cohort='QUEUED', entry=100, atr=2, vwap=99, vwap_source='exchange_atp',
                 or_high=99.5, or_low=97, opening_range_complete=True, reason='queued_not_filled')
        f.update(overrides)
        return f

    def rows(self):
        self.store.db.row_factory = __import__('sqlite3').Row
        return [dict(r) for r in self.store.db.execute('SELECT * FROM observations ORDER BY id')]

    def complete(self, f, price):
        self.store.candidate(f)
        at = f['observed_at'] + 1800
        self.store.quote(f['symbol'], price, at, at)

    def test_forward_only_no_early_exit_then_costs_and_slippage(self):
        f = self.features()
        self.store.candidate(f)
        self.store.quote('A', 103, f['observed_at'] + 1799, f['observed_at'] + 1799)
        self.assertEqual(self.rows()[0]['status'], 'PENDING')
        self.complete(f, 103)
        r = self.rows()[0]
        self.assertEqual(r['qty'], 100)
        self.assertEqual(r['gross'], 300)
        self.assertAlmostEqual(r['net'], 300 - 80 - 20300 * .0005)
        self.assertAlmostEqual(r['stress_net'], 300 - 160 - 20300 * .001)
        self.store.quote('A', 80, f['observed_at'] + 1810, f['observed_at'] + 1810)
        self.assertEqual(self.rows()[0]['exit_price'], 103)

    def test_short_sign_and_filter_symmetry(self):
        f = self.features(direction='SHORT', vwap=101, or_high=103, or_low=100.5)
        self.assertTrue(all(filters(f).values()))
        self.complete(f, 97)
        self.assertEqual(self.rows()[0]['gross'], 300)

    def test_future_price_timestamp_and_nan_are_not_usable(self):
        self.store.candidate(self.features(price_at=self.now.timestamp() + 1))
        self.store.candidate(self.features(symbol='B', entry=float('nan')))
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]['status'], 'ENTRY_PRICE_UNVERIFIED')

    def test_duplicate_keeps_original_features_and_separate_rejected_cohort(self):
        self.store.candidate(self.features())
        self.store.candidate(self.features(entry=110, vwap=105))
        self.store.candidate(self.features(cohort='SCORED_REJECTED', entry=101))
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(next(r for r in rows if r['cohort']=='QUEUED')['entry'], 100)

    def test_restart_resumes_pending_without_rewriting_snapshot(self):
        self.store.candidate(self.features())
        self.store.close()
        self.store = StudyStore(self.path)
        self.complete(self.features(entry=200), 102)
        self.assertEqual(self.rows()[0]['entry'], 100)
        self.assertEqual(self.rows()[0]['gross'], 200)

    def test_stale_unknown_or_pre_horizon_last_trade_cannot_close(self):
        f = self.features()
        self.store.candidate(f)
        for price_at in (None, f['observed_at'] + 1700, f['observed_at'] + 1799):
            self.store.quote('A', 110, f['observed_at'] + 1801, price_at)
        self.assertEqual(self.rows()[0]['status'], 'PENDING')
        self.store.expire(f['observed_at'] + 1861)
        self.assertEqual(self.rows()[0]['status'], 'MISSING_EXIT')
        self.assertIsNone(self.rows()[0]['net'])

    def test_exit_grace_inclusive_and_late_tick_never_fills(self):
        f = self.features()
        self.store.candidate(f)
        self.store.quote('A', 102, f['observed_at'] + 1861, f['observed_at'] + 1861)
        self.assertEqual(self.rows()[0]['status'], 'PENDING')
        self.store.quote('A', 101, f['observed_at'] + 1860, f['observed_at'] + 1860)
        self.assertEqual(self.rows()[0]['exit_price'], 101)

    def test_unknown_entry_and_oversized_share_not_zero_pnl(self):
        self.store.candidate(self.features(price_at=None))
        self.store.candidate(self.features(symbol='B', entry=10001))
        self.assertEqual({r['status'] for r in self.rows()}, {'ENTRY_PRICE_UNVERIFIED', 'UNSIZABLE'})
        self.assertTrue(all(r['net'] is None for r in self.rows()))

    def test_no_new_samples_after_1430_or_before_935(self):
        for hour, minute in ((9, 34), (14, 30), (15, 0)):
            now = self.now.replace(hour=hour, minute=minute).timestamp()
            self.store.candidate(self.features(observed_at=now, price_at=now))
        self.assertEqual(self.rows(), [])

    def test_missing_or_approximate_features_are_unknown(self):
        f = self.features(vwap_source='approximate_or_unknown', opening_range_complete=False)
        self.assertEqual(filters(f), dict(vwap_extension=None, opening_breakout=None, both=None))
        self.assertFalse(filters(self.features(vwap=95))['vwap_extension'])

    def test_paired_report_does_not_count_unknown_as_rejection(self):
        self.complete(self.features(), 103)
        self.complete(self.features(symbol='B', vwap=95), 99)
        self.complete(self.features(symbol='C', vwap_source='unknown'), 110)
        rows = self.rows()
        for r in rows: r['filters'] = json.loads(r['filters'])
        c = compare(rows)['filters']['vwap_extension']
        self.assertEqual((c['reference']['n'], c['kept']['n'], c['unknown']), (2, 1, 1))
        self.assertEqual(c['losers_removed'], 1)
        self.assertEqual(c['winners_removed'], 0)
        self.assertGreater(c['kept']['mean_net'], c['reference']['mean_net'])

    def test_report_shows_missed_winners_and_keeps_strategy_groups_separate(self):
        self.complete(self.features(vwap=95), 110)
        self.complete(self.features(strategy_id='changed'), 90)
        self.store.db.commit()
        groups, _, _ = read_report(self.path, '2026-09-25', '2026-09-25')
        self.assertEqual(len(groups), 2)
        row = next(rows for key, rows in groups.items() if key[1]=='frozen')
        self.assertEqual(compare(row)['filters']['vwap_extension']['winners_removed'], 1)
        out = io.StringIO()
        with contextlib.redirect_stdout(out): summarise(self.path, '2026-09-25', '2026-09-25')
        self.assertIn('NOT realised P&L', out.getvalue())
        self.assertIn('NOT portfolio drawdown', out.getvalue())

    def test_version_change_required_for_new_specification(self):
        self.store.close()
        self.store = None
        with patch.dict(SPEC, cost_allowance=99):
            with self.assertRaisesRegex(ValueError, 'new version'):
                StudyStore(self.path)

    def test_report_missing_file_is_read_only(self):
        missing = Path(self.temp.name) / 'missing.sqlite3'
        with self.assertRaises(__import__('sqlite3').OperationalError):
            read_report(missing, '2026-09-25', '2026-09-25')
        self.assertFalse(missing.exists())

    def test_backpressure_does_not_raise_or_mark_candidate_seen(self):
        observer = ShadowObserver.__new__(ShadowObserver)
        observer.events = queue.Queue(maxsize=1)
        observer.events.put(('full',))
        observer.failed = None
        observer.stop = __import__('threading').Event()
        observer.seen = set()
        observer.dropped = 0
        observer.candidate(self.features())
        self.assertEqual(observer.dropped, 1)
        self.assertFalse(observer.seen)

    def test_async_writer_failure_is_visible_and_does_not_escape(self):
        self.store.close()
        self.store = None
        with patch.object(StudyStore, 'candidate', side_effect=OSError('disk full')):
            observer = ShadowObserver(self.path)
            observer.candidate(self.features())
            observer.close()
        self.assertEqual(observer.failed, 'disk full')
        self.store = StudyStore(self.path)
        self.assertEqual(self.store.db.execute('SELECT error FROM observer_health').fetchone()[0], 'disk full')


class EngineShadowTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ExecutionTests()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.engine = self.f._make_signal_engine()

    def test_observer_failure_cannot_prevent_candidate_queue(self):
        e = self.engine
        e.shadow = Mock()
        e.shadow.candidate.side_effect = OSError('observer only')
        before = copy.deepcopy(self.f.manager.state)
        e._check_signal('A', 100, 1000, self.f.now, 'momentum')
        self.assertFalse(self.f.manager.inbox.empty())
        e._observe_shadow('A', 100, self.f.now, 'momentum', {'last_trade_time': self.f.now})
        self.assertEqual(self.f.manager.state, before)  # observer does not mutate journal
        self.assertTrue(e._shadow_error_logged)
        self.assertFalse(e.signals)

    def test_rejection_features_are_captured_before_future_outcome(self):
        e = self.engine
        e.shadow = Mock()
        e.all_stocks['A']['quality_score'] = 1
        e._check_signal('A', 100, 1000, self.f.now, 'momentum')
        e._observe_shadow('A', 100, self.f.now, 'momentum', {'last_trade_time': self.f.now})
        f = e.shadow.candidate.call_args.args[0]
        self.assertEqual(f['cohort'], 'SCORED_REJECTED')
        self.assertEqual(f['quality'], 1)
        self.assertFalse(f['breadth_at_entry_verified'])
        self.assertFalse(f['opening_range_complete'])

    def test_candidate_queued_not_filled_and_opening_range_coverage(self):
        e = self.engine
        e.shadow = Mock()
        e._shadow_opening['A'] = dict(first=555, last=569, max_gap=30)
        e._check_signal('A', 100, 1000, self.f.now, 'momentum')
        e._observe_shadow('A', 100, self.f.now, 'momentum', {'last_trade_time': self.f.now})
        f = e.shadow.candidate.call_args.args[0]
        self.assertEqual(f['cohort'], 'QUEUED')
        self.assertTrue(f['opening_range_complete'])
        self.assertEqual(f['reason'], 'queued_not_filled')
        self.assertEqual(self.f.manager.snapshot()['tickets'], 0)

    def test_tick_hook_leaves_real_candidate_unchanged_even_if_shadow_filter_fails(self):
        e = self.engine
        now = self.f.now
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return now
        tick = dict(instrument_token=1, last_price=100, volume_traded=1000,
                    ohlc=dict(open=100, close=98), average_traded_price=90,
                    last_trade_time=now)
        e.shadow = Mock()
        with patch.dict(e._on_tick_locked.__globals__, datetime=Clock):
            e._on_tick_locked(None, [tick])
        recorded = e.shadow.candidate.call_args.args[0]
        candidate = self.f.manager.inbox.get_nowait()
        self.assertEqual(recorded['cohort'], 'QUEUED')
        self.assertFalse(filters(recorded)['vwap_extension'])  # experimental rejection
        self.assertEqual(candidate['symbol'], 'A')  # baseline still queued
        self.assertEqual(candidate['entry'], 100)
        self.assertGreater(candidate['position_size'], 0)
        self.assertEqual(self.f.manager.snapshot()['tickets'], 0)

    def test_exit_prices_continue_to_observer_when_new_entries_blocked(self):
        e = self.engine
        e.shadow = Mock()
        now = self.f.now
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return now
        with patch.object(e.execution, 'snapshot', return_value={'ready':False}):
            with patch.dict(e._on_tick_locked.__globals__, datetime=Clock):
                e._on_tick_locked(None, [dict(instrument_token=1, last_price=102,
                    last_trade_time=now, volume_traded=1000)])
        e.shadow.candidate.assert_not_called()
        e.shadow.quote.assert_called_once_with('A', 102, now.timestamp(), now.timestamp())

    def test_late_start_opening_gap_leaves_breakout_unknown(self):
        e = self.engine
        e.shadow = Mock()
        e._shadow_opening['A'] = dict(first=560, last=569, max_gap=30)
        e._check_signal('A', 100, 1000, self.f.now, 'momentum')
        e._observe_shadow('A', 100, self.f.now, 'momentum', {'last_trade_time': self.f.now})
        self.assertIsNone(filters(e.shadow.candidate.call_args.args[0])['opening_breakout'])


if __name__ == '__main__':
    unittest.main()
