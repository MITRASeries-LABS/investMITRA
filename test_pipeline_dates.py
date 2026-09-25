"""Offline regressions for delayed EOD workflow dates and missing daily inputs."""
import argparse
import __future__
import ast
import contextlib
from datetime import date, datetime, timedelta, timezone
import io
import logging
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).parent / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from pipeline_date import IST, resolve_trade_date
from verify_pipeline_data import validate_counts, verify


def load_function(filename, name, **namespace):
    tree = ast.parse((SCRIPTS / filename).read_text(encoding='utf-8'))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
    namespace.update(argparse=argparse, date=date, datetime=datetime, timedelta=timedelta,
                     os=os, IST=IST, logger=logging.getLogger('pipeline-test'))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, 'exec',
                 flags=__future__.annotations.compiler_flag), namespace)
    return namespace[name]


class PipelineDateTests(unittest.TestCase):
    def test_evening_keeps_same_day_including_after_23(self):
        for hour in (18, 20, 22, 23):
            self.assertEqual(resolve_trade_date(now=datetime(2026,9,24,hour,30,tzinfo=IST)), date(2026,9,24))

    def test_delayed_midnight_and_morning_use_previous_day(self):
        for hour in (0, 1, 5, 8, 17):
            self.assertEqual(resolve_trade_date(now=datetime(2026,9,25,hour,21,tzinfo=IST)), date(2026,9,24))

    def test_actual_failed_run_utc_timestamp_resolves_24(self):
        self.assertEqual(resolve_trade_date(now=datetime.fromisoformat('2026-09-24T18:51:28+00:00')), date(2026,9,24))

    def test_weekend_and_monday_morning_resolve_friday(self):
        for day in (26,27,28):
            self.assertEqual(resolve_trade_date(now=datetime(2026,9,day,8,tzinfo=IST)), date(2026,9,25))

    def test_explicit_recovery_date_is_preserved(self):
        self.assertEqual(resolve_trade_date('2026-09-22', now=datetime(2026,9,25,8,tzinfo=IST)), date(2026,9,22))
        self.assertEqual(resolve_trade_date(date(2026,9,23)), date(2026,9,23))

    def test_naive_time_and_invalid_explicit_date_rejected(self):
        with self.assertRaises(ValueError): resolve_trade_date(now=datetime(2026,9,25))
        with self.assertRaises(ValueError): resolve_trade_date('not-a-date')

    def test_cli_recovery_date_and_range_end(self):
        for args in (['--date','2026-09-24'], ['--start','2026-09-22','--end','2026-09-24']):
            result = subprocess.run([sys.executable,str(SCRIPTS/'pipeline_date.py'),*args],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),'2026-09-24')

    def test_invalid_range_fails_before_workflow_writes_env(self):
        result = subprocess.run([sys.executable,str(SCRIPTS/'pipeline_date.py'),'--start','2026-09-25','--end','2026-09-24'],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(result.stdout,'')


class PipelineReadinessTests(unittest.TestCase):
    def test_empty_or_mixed_date_inputs_fail(self):
        for total, matching in ((0,0),(12,0),(12,11)):
            with self.subTest(total=total,matching=matching), self.assertRaises(RuntimeError):
                validate_counts(date(2026,9,24),total,matching,'prices')
        validate_counts(date(2026,9,24),12,12,'prices')

    def test_verifier_reads_exact_partition_and_closes_on_error(self):
        for stage, filename in [('prices','day=24/nse_bhavcopy_*.parquet'),
                                ('features','price_features_20260924.parquet'),
                                ('momentum','momentum_20260924.parquet')]:
            con=Mock()
            con.execute.return_value.fetchone.return_value=(10,10)
            fake=SimpleNamespace(get_duckdb_con=lambda:con,BUCKET='test-bucket',ENV='prod')
            with patch.dict(sys.modules,{'compute_features':fake}), contextlib.redirect_stdout(io.StringIO()):
                verify(date(2026,9,24),stage)
                params=con.execute.call_args.args[1]
                self.assertEqual(params[0],date(2026,9,24))
                self.assertTrue(params[1].endswith(filename))
                con.close.assert_called_once()
                con.reset_mock()
                con.execute.side_effect=RuntimeError('No parquet files')
                with self.assertRaises(RuntimeError):verify(date(2026,9,24),stage)
                con.close.assert_called_once()

    def test_price_loader_honours_env_date_and_rejects_zero_nse(self):
        loader=Mock(return_value={'nse_rows':0,'bse_rows':5000})
        report=Mock()
        main=load_function('load_prices_to_neon.py','main',resolve_trade_date=resolve_trade_date,load_date=loader,verify=report)
        with patch.dict(os.environ,{'TRADE_DATE':'2026-09-24'}), patch.object(sys,'argv',['load','--require-nse']):
            with self.assertRaisesRegex(RuntimeError,'No NSE prices'):main()
        loader.assert_called_once_with(date(2026,9,24))
        report.assert_not_called()

    def test_price_loader_cli_overrides_env_and_accepts_rows(self):
        loader=Mock(return_value={'nse_rows':2400,'bse_rows':5000})
        main=load_function('load_prices_to_neon.py','main',resolve_trade_date=resolve_trade_date,load_date=loader,verify=Mock())
        with patch.dict(os.environ,{'TRADE_DATE':'2026-09-25'}), patch.object(sys,'argv',['load','--date','2026-09-24','--require-nse']):main()
        loader.assert_called_once_with(date(2026,9,24))

    def test_missing_current_momentum_cannot_fall_back_to_old_file(self):
        con=Mock()
        con.execute.side_effect=RuntimeError('404 missing current file')
        fn=load_function('compute_investmitra_score.py','load_score',BUCKET='test',ENV='prod')
        self.assertIsNone(fn(con,'momentum',date(2026,9,24)))
        self.assertEqual(con.execute.call_count,1)
        self.assertIn('momentum_20260924.parquet',con.execute.call_args.args[0])

    def test_slower_component_fallback_is_retained(self):
        con=Mock()
        frame=[{'isin':'test'}]
        con.execute.side_effect=[RuntimeError('missing'),SimpleNamespace(df=lambda:frame)]
        fn=load_function('compute_investmitra_score.py','load_score',BUCKET='test',ENV='prod')
        self.assertIs(fn(con,'financial_stress',date(2026,9,24)),frame)

    def test_empty_composite_fails_instead_of_loading_existing_stale_output(self):
        main=load_function('compute_investmitra_score.py','main',compute_investmitra_score=Mock(return_value=SimpleNamespace(empty=True)))
        with patch.object(sys,'argv',['score','--date','2026-09-24']), self.assertRaises(RuntimeError):main()

    def test_zero_score_writes_fail(self):
        main=load_function('load_scores_to_neon.py','main',ensure_table=Mock(),load_for_date=Mock(return_value=0))
        with patch.object(sys,'argv',['load','--date','2026-09-24']), self.assertRaises(RuntimeError):main()


if __name__=='__main__':unittest.main()
