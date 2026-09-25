"""Server pipeline tests: mocked subprocesses, no database, broker or alerts."""
from datetime import date, datetime
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
import yaml

ROOT=Path(__file__).parent
sys.path.insert(0,str(ROOT/'scripts'))
import run_server_pipeline as runner
import pipeline_heartbeat as heartbeat
from pipeline_date import IST

DAY=date(2026,9,24)


class ServerPipelineTests(unittest.TestCase):
    def test_price_stages_have_explicit_date_and_require_nse(self):
        commands=runner.price_commands(DAY)
        self.assertEqual(len(commands),6)
        self.assertEqual(commands[-1],['scripts/load_prices_to_neon.py','--date',str(DAY),'--require-nse'])

    def test_all_feature_workflow_programs_preserved(self):
        workflow=yaml.load((ROOT/'.github/workflows/feature_engineering.yml').read_text(encoding='utf-8'),Loader=yaml.BaseLoader)
        import re
        programs=set(re.findall(r'python (scripts/\w+\.py)', '\n'.join(s.get('run','') for s in workflow['jobs']['compute-features']['steps'])))
        programs-={'scripts/pipeline_date.py','scripts/pipeline_readiness.py'}
        self.assertTrue(programs <= {c[0] for c in runner.score_commands(DAY)}, programs)
        commands=runner.score_commands(DAY)
        self.assertEqual(sum(c[0]=='scripts/daily_top_picks.py' for c in commands),2)
        self.assertFalse(any('order_manager' in str(c) or 'intraday_signals' in str(c) for c in commands))

    def test_plan_runs_without_credentials_or_services(self):
        result=subprocess.run([sys.executable,str(ROOT/'scripts/run_server_pipeline.py'),'--plan','--date',str(DAY)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('compute_management_quality_score.py --date 2026-09-24',result.stdout)

    def test_ready_data_never_reexecutes_pipeline(self):
        with patch.object(runner,'inspect',return_value=(True,[])),patch.object(runner,'execute') as execute:
            runner.prepare(DAY,[DAY]);execute.assert_not_called()

    def test_check_only_never_starts_recovery(self):
        with patch.object(runner,'inspect',return_value=(False,[DAY])),patch.object(runner,'execute') as execute:
            with self.assertRaises(RuntimeError):runner.prepare(DAY,[DAY],True)
            execute.assert_not_called()

    def test_late_start_is_blocked(self):
        with patch.object(runner,'inspect',return_value=(False,[DAY])),patch.object(runner,'recovery_allowed',return_value=False),patch.object(runner,'execute') as execute:
            with self.assertRaises(RuntimeError):runner.prepare(DAY,[DAY])
            execute.assert_not_called()

    def test_missing_sessions_precede_scoring_and_receipt(self):
        missing=[date(2026,9,22),DAY]
        with patch.object(runner,'inspect',side_effect=[(False,missing),(True,[])]) as inspect,patch.object(runner,'recovery_allowed',return_value=True),patch.object(runner,'execute') as execute:
            runner.prepare(DAY,missing)
            self.assertEqual([call.args[1] for call in execute.call_args_list],[missing[0],DAY,DAY])
            self.assertEqual(execute.call_args_list[-1].args[0],runner.score_commands(DAY))
            self.assertEqual(inspect.call_args.kwargs,{'mark':True})

    def test_failed_command_stops_before_scoring_and_receipt(self):
        with patch.object(runner,'inspect',return_value=(False,[DAY])) as inspect,patch.object(runner,'recovery_allowed',return_value=True),patch.object(runner,'execute',side_effect=RuntimeError('failed')) as execute:
            with self.assertRaises(RuntimeError):runner.prepare(DAY,[DAY])
            execute.assert_called_once();inspect.assert_called_once()

    def test_execution_date_is_shared_and_timeout_enforced(self):
        run=Mock()
        with patch.object(runner.time,'monotonic',return_value=50),contextlib.redirect_stdout(io.StringIO()):
            runner.execute([['-m','example']],DAY,100,run=run)
        args=run.call_args
        self.assertEqual(args.kwargs['env']['TRADE_DATE'],str(DAY))
        self.assertEqual(args.kwargs['timeout'],50)
        self.assertTrue(args.kwargs['check'])

    def test_expired_budget_never_launches_command(self):
        run=Mock()
        with patch.object(runner.time,'monotonic',return_value=101):
            with self.assertRaises(TimeoutError):runner.execute([['-m','example']],DAY,100,run=run)
        run.assert_not_called()

    def test_external_monitor_required_for_morning_check(self):
        with patch.dict(os.environ,{},clear=True),patch.object(heartbeat,'urlopen') as call:
            with self.assertRaises(RuntimeError):heartbeat.ping('PIPELINE_HEARTBEAT_SUCCESS_URL',required=True)
            call.assert_not_called()

    def test_heartbeat_never_exposes_secret_url(self):
        with patch.dict(os.environ,{'PING':'https://monitor.example/secret'}),patch.object(heartbeat,'urlopen',side_effect=OSError('https://monitor.example/secret')):
            with self.assertRaises(RuntimeError) as error:heartbeat.ping('PING')
            self.assertNotIn('secret',str(error.exception))

    def test_timers_do_not_depend_on_github_or_each_other(self):
        folder=ROOT/'deploy/systemd'
        data=(folder/'investmitra-data.timer').read_text()
        morning=(folder/'investmitra-readiness.timer').read_text()
        self.assertEqual(data.count('OnCalendar='),4)
        self.assertIn('07:00:00 Asia/Kolkata',morning)
        for name in ('investmitra-data.service','investmitra-readiness.service'):
            content=(folder/name).read_text()
            self.assertNotIn('github',content.lower())
            self.assertIn('OnFailure=investmitra-data-failure.service',content)
            self.assertNotIn('After=investmitra-data.service',content)
            self.assertIn('TimeoutStartSec=',content)
        self.assertIn('Type=oneshot',(folder/'investmitra-data.service').read_text())


if __name__=='__main__':unittest.main()
