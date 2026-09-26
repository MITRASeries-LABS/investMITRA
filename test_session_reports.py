"""Automatic post-close reports use saved session data without trading effects."""
import contextlib
import copy
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_auto_trading as fixtures
from session_reports import run_session_reports
from shadow_validation import StudyStore


class SessionReportsTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ExecutionTests()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.execution = self.f.manager
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.shadow_path = Path(self.temp.name) / 'custom-shadow.sqlite3'
        store = StudyStore(self.shadow_path)
        store.close()
        self.execution.shadow_observer = SimpleNamespace(
            path=str(self.shadow_path), failed=None, worker=Mock(is_alive=Mock(return_value=False)))

    def capture(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = run_session_reports(self.execution)
        return result, output.getvalue()

    def test_both_real_reports_run_for_saved_day_without_mutating_journal(self):
        state = copy.deepcopy(self.execution.state)
        raw = self.f.journal.db.execute('SELECT body FROM state WHERE id=1').fetchone()[0]
        result, text = self.capture()
        self.assertEqual(result, {'execution': 'ok', 'shadow': 'ok'})
        self.assertIn('AUTO-PAPER SUMMARY — 2026-09-10', text)
        self.assertIn('SHADOW CANDIDATE STUDY 2026-09-10 to 2026-09-10', text)
        self.assertIn('No observations in this date range', text)
        self.assertEqual(self.execution.state, state)
        self.assertEqual(self.f.journal.db.execute('SELECT body FROM state WHERE id=1').fetchone()[0], raw)

    def test_actual_configured_paths_and_session_date_are_used(self):
        with patch('auto_paper_summary.summarise') as trade:
            with patch('shadow_validation_report.summarise') as shadow:
                result, _ = self.capture()
        self.assertEqual(result, {'execution': 'ok', 'shadow': 'ok'})
        trade.assert_called_once_with(self.f.journal.path, '2026-09-10')
        shadow.assert_called_once_with(str(self.shadow_path), '2026-09-10', '2026-09-10')

    def test_trade_report_failure_does_not_skip_shadow(self):
        with patch('auto_paper_summary.summarise', side_effect=ValueError('bad data')):
            result, text = self.capture()
        self.assertEqual(result, {'execution': 'failed', 'shadow': 'ok'})
        self.assertIn('SHADOW CANDIDATE STUDY', text)

    def test_missing_shadow_file_does_not_create_it_or_skip_trade_report(self):
        missing = Path(self.temp.name) / 'never-created.sqlite3'
        self.execution.shadow_observer.path = str(missing)
        result, text = self.capture()
        self.assertEqual(result, {'execution': 'ok', 'shadow': 'failed'})
        self.assertIn('AUTO-PAPER SUMMARY', text)
        self.assertFalse(missing.exists())

    def test_disabled_or_not_started_observer_does_not_read_old_default_database(self):
        self.execution.shadow_observer = None
        with patch('shadow_validation_report.summarise') as shadow:
            result, _ = self.capture()
        self.assertEqual(result, {'execution': 'ok', 'shadow': 'skipped'})
        shadow.assert_not_called()

    def test_draining_shadow_writer_does_not_produce_premature_final_report(self):
        self.execution.shadow_observer.worker.is_alive.return_value = True
        with patch('shadow_validation_report.summarise') as shadow:
            result, _ = self.capture()
        self.assertEqual(result, {'execution': 'ok', 'shadow': 'skipped'})
        shadow.assert_not_called()

    def test_failed_capture_is_flagged_before_existing_results_are_printed(self):
        self.execution.shadow_observer.failed = 'disk error'
        with self.assertLogs('session_reports', level='WARNING') as logs:
            result, text = self.capture()
        self.assertEqual(result['shadow'], 'ok')
        self.assertIn('incomplete coverage', '\n'.join(logs.output))
        self.assertIn('NOT realised P&L', text)

    def test_open_execution_never_prints_final_reports(self):
        with patch.object(self.execution, 'snapshot', return_value={'flat': False, 'day': '2026-09-10'}):
            result, text = self.capture()
        self.assertEqual(result, {'execution': 'skipped', 'shadow': 'skipped'})
        self.assertEqual(text, '')

    def test_missing_date_never_falls_back_to_today(self):
        with patch.object(self.execution, 'snapshot', return_value={'flat': True}):
            result, text = self.capture()
        self.assertEqual(result, {'execution': 'skipped', 'shadow': 'skipped'})
        self.assertEqual(text, '')


if __name__ == '__main__':
    unittest.main()
