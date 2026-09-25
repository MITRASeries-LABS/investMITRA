"""Offline coverage for overnight orchestration, receipts and early failure reports."""
import contextlib
from datetime import date, datetime
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
import yaml

sys.path.insert(0, str(Path(__file__).parent/'scripts'))
import pipeline_readiness as readiness
import pipeline_readiness_report as report
from pipeline_date import IST

DAY = date(2026, 9, 24)
HOLIDAYS = {date(2026, 1, 26), date(2026, 9, 23)}
ROOT = Path(__file__).parent


class OvernightReadinessTests(unittest.TestCase):
    def test_evening_and_delayed_next_morning_resolve_same_session(self):
        for now in (datetime(2026,9,24,20,37,tzinfo=IST),datetime(2026,9,25,4,7,tzinfo=IST)):
            self.assertEqual(readiness.completed_session(now,HOLIDAYS),DAY)

    def test_holiday_uses_preceding_session_without_bypass(self):
        self.assertEqual(readiness.completed_session(datetime(2026,9,24,4,tzinfo=IST),HOLIDAYS),date(2026,9,22))

    def test_monday_morning_uses_friday(self):
        self.assertEqual(readiness.completed_session(datetime(2026,9,28,4,tzinfo=IST),HOLIDAYS),date(2026,9,25))

    def test_missing_calendar_year_rejected(self):
        with self.assertRaises(ValueError): readiness.completed_session(datetime(2027,1,5,4,tzinfo=IST),HOLIDAYS)
        with self.assertRaises(ValueError): readiness.recent_sessions(date(2026,1,2),HOLIDAYS)

    def test_recovery_cutoff_blocks_late_scheduled_jobs(self):
        for hour,minute,allowed in [(4,7,True),(5,59,True),(6,0,False),(7,7,False),(8,30,False),(18,0,True),(23,37,True)]:
            self.assertEqual(readiness.recovery_allowed(datetime(2026,9,25,hour,minute,tzinfo=IST)),allowed)

    def test_recent_sessions_are_ordered_and_skip_holidays(self):
        sessions=readiness.recent_sessions(DAY,HOLIDAYS)
        self.assertEqual(len(sessions),5)
        self.assertEqual(sessions[-1],DAY)
        self.assertEqual(sessions,sorted(sessions))
        self.assertNotIn(date(2026,9,23),sessions)
        self.assertTrue(all(d.weekday()<5 for d in sessions))

    def test_receipt_requires_date_objects_and_database_counts(self):
        objects={'features':'etag-1'};counts={'prices':2400,'scores':4900,'history':50000}
        receipt=dict(version=1,date=str(DAY),objects=objects,counts=counts)
        self.assertTrue(readiness.receipt_matches(receipt,DAY,objects,counts))
        for changed in [None,dict(receipt,date='2026-09-21'),dict(receipt,version=0),dict(receipt,objects={}),dict(receipt,counts={})]:
            self.assertFalse(readiness.receipt_matches(changed,DAY,objects,counts))
        self.assertFalse(readiness.receipt_matches(receipt,DAY,{'features':'changed'},counts))

    def test_missing_output_cannot_generate_fingerprint(self):
        client=Mock();client.get_paginator.return_value.paginate.return_value=[{'Contents':[]}]
        with self.assertRaises(RuntimeError): readiness.lake_fingerprint(client,'bucket','prod',DAY)

    def test_fingerprint_reads_all_four_producer_outputs(self):
        client=Mock()
        client.get_paginator.return_value.paginate.side_effect=lambda **kw:[{'Contents':[{'Key':kw['Prefix']+'file.parquet' if kw['Prefix'].endswith('/') else kw['Prefix'],'ETag':'abc'}]}]
        self.assertEqual(len(readiness.lake_fingerprint(client,'bucket','prod',DAY)),4)

    def inspect_with(self,counts,missing,mark=False,verify_error=None,receipt=None):
        client=Mock();validator=Mock(side_effect=verify_error)
        with patch.object(readiness,'lake_client',return_value=client), patch.object(readiness,'database_counts',return_value=(counts,missing)), patch.object(readiness,'lake_fingerprint',return_value={'key':'etag'}), patch.object(readiness,'read_receipt',return_value=receipt), patch.dict(sys.modules,{'verify_pipeline_data':SimpleNamespace(verify=validator)}):
            result=readiness.inspect(DAY,[DAY],mark)
        return result,client,validator

    def test_missing_sessions_request_recovery_not_ready(self):
        result,client,validator=self.inspect_with({'scores':0,'prices':0,'history':0},[DAY])
        self.assertEqual(result,(False,[DAY]));client.put_object.assert_not_called();validator.assert_not_called()

    def test_failed_validation_cannot_publish_receipt(self):
        with self.assertRaises(RuntimeError): self.inspect_with({'scores':10,'prices':10,'history':20000},[],True,RuntimeError('stale'))

    def test_no_receipt_means_rebuild_even_when_dates_look_current(self):
        result,client,validator=self.inspect_with({'scores':10,'prices':10,'history':20000},[])
        self.assertEqual(result,(False,[]));self.assertEqual(validator.call_count,4);client.put_object.assert_not_called()

    def test_success_receipt_written_only_after_validation(self):
        result,client,validator=self.inspect_with({'scores':10,'prices':10,'history':20000},[],True)
        self.assertEqual(result,(True,[]));self.assertEqual(validator.call_count,4)
        receipt=json.loads(client.put_object.call_args.kwargs['Body'])
        self.assertEqual(receipt['date'],str(DAY));self.assertEqual(receipt['objects'],{'key':'etag'})

    def test_database_check_is_readonly_and_requires_each_session(self):
        conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value=[(DAY,2404)];cur.fetchone.side_effect=[(4900,),(250000,)]
        fake=SimpleNamespace(connect=Mock(return_value=conn))
        with patch.dict(os.environ,{'CC_POSTGRES_URL':'fake'}),patch.dict(sys.modules,{'psycopg2':fake}):
            counts,missing=readiness.database_counts(DAY,[date(2026,9,22),DAY])
        self.assertEqual(missing,[date(2026,9,22)]);self.assertEqual(counts['prices'],2404)
        conn.set_session.assert_called_once_with(readonly=True);conn.close.assert_called_once()

    def test_ready_overnight_report_does_not_send_repeated_alerts(self):
        with patch.dict(os.environ,{'READY':'true','MORNING':'false','GITHUB_REPOSITORY':'test/repo','GITHUB_RUN_ID':'1','GITHUB_STEP_SUMMARY':''}),patch.object(report,'urlopen') as send,contextlib.redirect_stdout(io.StringIO()):
            report.main();send.assert_not_called()

    def test_failed_notification_never_exposes_token(self):
        with patch.dict(os.environ,{'READY':'false','TELEGRAM_BOT_TOKEN':'secret-token','TELEGRAM_CHAT_ID':'123','GITHUB_REPOSITORY':'test/repo','GITHUB_RUN_ID':'1','GITHUB_STEP_SUMMARY':''}),patch.object(report,'urlopen',side_effect=OSError('secret-token')),contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError) as error:report.main()
            self.assertNotIn('secret-token',str(error.exception))


class WorkflowWiringTests(unittest.TestCase):
    def load(self,name):
        return yaml.load((ROOT/'.github/workflows'/name).read_text(encoding='utf-8'),Loader=yaml.BaseLoader)

    def test_only_orchestrator_schedules_pipeline_and_calls_in_order(self):
        main=self.load('overnight_readiness.yml')
        self.assertEqual(len(main['on']['schedule']),5)
        self.assertEqual(main['jobs']['prices']['needs'],'readiness')
        self.assertEqual(main['jobs']['scores']['needs'],['readiness','prices'])
        self.assertEqual(main['jobs']['prices']['strategy']['max-parallel'],'1')
        for name in ('market_data.yml','feature_engineering.yml'):
            workflow=self.load(name)
            self.assertNotIn('schedule',workflow['on'])
            self.assertIn('workflow_call',workflow['on']);self.assertIn('workflow_dispatch',workflow['on'])
            self.assertEqual(workflow['concurrency']['cancel-in-progress'],'false')

    def test_morning_check_is_independent_and_cannot_start_rebuild(self):
        workflow=self.load('overnight_readiness.yml')
        self.assertIn('morning-check',workflow['concurrency']['group'])
        check=next(s for s in workflow['jobs']['readiness']['steps'] if s.get('id')=='check')
        self.assertIn('37 1 * * 1-5',check['env']['CHECK_ONLY'])
        self.assertIn('--check-only',check['run'])
        self.assertEqual(workflow['jobs']['report']['if'],'always()')

    def test_automatic_child_workflows_enforce_cutoff(self):
        for name in ('market_data.yml','feature_engineering.yml'):
            workflow=self.load(name)
            matches=[step for job in workflow['jobs'].values() for step in job.get('steps',[]) if '--check-cutoff' in step.get('run','')]
            self.assertEqual(len(matches),1)
            self.assertEqual(matches[0]['if'],'inputs.automatic')


if __name__=='__main__':unittest.main()
