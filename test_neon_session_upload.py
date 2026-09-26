"""Offline tests: no network, credentials or production database required."""
import ast
import copy
import logging
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import test_auto_trading as fixtures
import order_manager


class SessionUploadTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ExecutionTests()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.manager = self.f.manager

    def test_success_uploads_once_without_sleep_or_journal_mutation(self):
        before = copy.deepcopy(self.manager.state)
        with patch.dict(os.environ, CC_POSTGRES_URL='test-only'):
            with patch.object(self.manager, '_mirror_snapshot_to_neon', return_value=True) as write:
                with patch('order_manager.time.sleep') as sleep:
                    with self.assertLogs('order_manager', level='INFO') as logs:
                        self.assertTrue(self.manager.mirror_to_neon())
        write.assert_called_once()
        sleep.assert_not_called()
        self.assertEqual(self.manager.state, before)
        self.assertIn('upload succeeded', '\n'.join(logs.output))

    def test_retry_is_bounded_and_reuses_exact_snapshot(self):
        with patch.dict(os.environ, CC_POSTGRES_URL='test-only'):
            with patch.object(self.manager, '_mirror_snapshot_to_neon', side_effect=[False, False, True]) as write:
                with patch('order_manager.time.sleep') as sleep:
                    self.assertTrue(self.manager.mirror_to_neon())
        self.assertEqual(write.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 5])
        self.assertEqual(write.call_args_list[0], write.call_args_list[2])

    def test_total_failure_is_explicit_and_retains_durable_journal(self):
        before = self.f.journal.db.execute('SELECT body FROM state WHERE id=1').fetchone()[0]
        with patch.dict(os.environ, CC_POSTGRES_URL='test-only'):
            with patch.object(self.manager, '_mirror_snapshot_to_neon', return_value=False) as write:
                with patch('order_manager.time.sleep'):
                    with self.assertLogs('order_manager', level='ERROR') as logs:
                        self.assertFalse(self.manager.mirror_to_neon())
        self.assertEqual(write.call_count, 3)
        self.assertEqual(self.f.journal.db.execute('SELECT body FROM state WHERE id=1').fetchone()[0], before)
        self.assertIn('upload incomplete', '\n'.join(logs.output))

    def test_no_configuration_skips_without_retries(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(self.manager, '_mirror_snapshot_to_neon') as write:
                with self.assertLogs('order_manager', level='WARNING'):
                    self.assertFalse(self.manager.mirror_to_neon())
        write.assert_not_called()

    def test_commit_failure_is_not_reported_as_success_or_leaked(self):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.__exit__.side_effect = RuntimeError('password=do-not-print-this')
        pg = SimpleNamespace(connect=Mock(return_value=conn))
        with patch.dict(sys.modules, psycopg2=pg):
            with self.assertLogs('order_manager', level='WARNING') as logs:
                self.assertFalse(self.manager._mirror_snapshot_to_neon('secret-url', self.manager.snapshot()))
        self.assertNotIn('do-not-print-this', '\n'.join(logs.output))
        conn.close.assert_called_once()

    def test_transaction_commits_before_success_and_uses_day_scoped_upsert(self):
        conn = MagicMock()
        conn.__enter__.return_value = conn
        cur = conn.cursor.return_value.__enter__.return_value
        pg = SimpleNamespace(connect=Mock(return_value=conn))
        view = self.manager.snapshot()
        with patch.dict(sys.modules, psycopg2=pg):
            self.assertTrue(self.manager._mirror_snapshot_to_neon('test-only', view))
        query, params = cur.execute.call_args.args
        self.assertIn('ON CONFLICT (account_id,execution_mode,trade_date)', query)
        self.assertEqual(params[:3], (view['account'], view['mode'], view['day']))
        conn.__exit__.assert_called_once_with(None, None, None)
        conn.close.assert_called_once()

    def run_main(self, alive=False, upload_error=False, report_error=False):
        source = Path('scripts/intraday_signals.py').read_text(encoding='utf-8')
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name=='main')
        timeline = []
        execution = Mock()
        execution.snapshot.return_value = {'flat': True}
        execution.report.return_value = 'Execution flat=True'
        execution.request_flatten.side_effect = lambda: timeline.append('flatten')
        execution.stop_requested.set.side_effect = lambda: timeline.append('stop')
        def upload():
            timeline.append('upload')
            if upload_error: raise OSError('upload failed unexpectedly')
        execution.mirror_to_neon.side_effect = upload
        def reports(_):
            timeline.append('reports')
            if report_error: raise ValueError('bad report')
        execution.journal.close.side_effect = lambda: timeline.append('close')
        worker = Mock()
        worker.join.side_effect = lambda **kw: timeline.append('join')
        worker.is_alive.return_value = alive
        ns = dict(__file__=str(Path('scripts/intraday_signals.py').resolve()), os=os,
                  logger=logging.getLogger('test-main'), API_KEY='test', ACCESS_TOKEN='test',
                  BUILD_ID='test', EXECUTION_MODE='auto_paper', RateLimitedKite=lambda kite: kite,
                  KiteConnect=Mock(return_value=Mock()), MAX_DAILY_CAPITAL_INR=35000,
                  MIN_TICKET_INR=1000, MAX_CAPITAL_PER_TRADE=10000, MAX_RISK_PER_TRADE_INR=1500,
                  MAX_DAILY_LOSS_INR=1500, MAX_POSITIONS=3,
                  _run_signals=lambda *args: timeline.append('session'))
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'main-under-test', 'exec'), ns)
        with patch('order_manager.build_executor', return_value=execution):
            with patch('threading.Thread', return_value=worker) as thread:
                with patch('session_reports.run_session_reports', side_effect=reports):
                    if upload_error:
                        with self.assertRaises(OSError): ns['main']()
                    else:
                        ns['main']()
        self.assertEqual(thread.call_count, 1)  # execution worker only; no uploader thread
        return timeline

    def test_main_uploads_only_after_session_and_worker_stop(self):
        self.assertEqual(self.run_main(), ['session', 'flatten', 'stop', 'join', 'upload', 'reports', 'close'])

    def test_still_running_worker_cannot_upload_final_snapshot_or_close_journal(self):
        self.assertEqual(self.run_main(alive=True), ['session', 'flatten', 'stop', 'join'])

    def test_upload_exception_cannot_skip_reports_or_journal_cleanup(self):
        self.assertEqual(self.run_main(upload_error=True),
                         ['session', 'flatten', 'stop', 'join', 'upload', 'reports', 'close'])

    def test_report_exception_cannot_skip_journal_cleanup(self):
        self.assertEqual(self.run_main(report_error=True),
                         ['session', 'flatten', 'stop', 'join', 'upload', 'reports', 'close'])


if __name__ == '__main__':
    unittest.main()
