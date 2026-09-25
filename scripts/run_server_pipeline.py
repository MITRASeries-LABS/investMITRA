"""Execute EOD preparation directly on Linux, without GitHub scheduling/runners.

Only ingestion/scoring commands are permitted here. No trading engine or broker.
"""
import argparse
from datetime import date, datetime
import os
from pathlib import Path
import subprocess
import sys
import time

from pipeline_date import IST
from pipeline_readiness import completed_session, recent_sessions, recovery_allowed, inspect
from signal_runtime import load_nse_holidays

ROOT = Path(__file__).resolve().parent.parent


def price_commands(target):
    commands = [['-m', 'src.connectors.flows.' + name] for name in (
        'ingest_nse_bhavcopy', 'ingest_nse_delivery', 'ingest_bse_eod',
        'ingest_nse_fo_bhavcopy', 'eod_processing')]
    return commands + [['scripts/load_prices_to_neon.py', '--date', str(target), '--require-nse']]


def score_commands(target):
    dated = lambda name: ['scripts/'+name+'.py', '--date', str(target)]
    validate = lambda stage: dated('verify_pipeline_data') + ['--stage', stage]
    commands = [validate('prices'), dated('compute_features'), validate('features'),
                dated('compute_momentum_score'), validate('momentum'),
                dated('compute_financial_stress_score'), dated('compute_management_quality_score'),
                dated('compute_investmitra_score'), validate('composite'),
                dated('load_scores_to_neon'), dated('compute_early_signals')]
    commands += [['scripts/'+name+'.py'] for name in (
        'fetch_screener_signals', 'fetch_corporate_events', 'fetch_nse_announcements',
        'fetch_sebi_rss', 'fetch_fo_stocks', 'fetch_market_indices', 'fetch_global_sentiment')]
    commands += [dated('daily_top_picks') + ['--top','10','--cap',cap,'--no-ta']
                 for cap in ('ALL','SMALLMICRO')]
    return commands + [['scripts/trade_analyzer.py']]


def execute(commands, target, deadline, run=subprocess.run):
    env = dict(os.environ, TRADE_DATE=str(target), CC_ENV='prod',
               CC_DB_SCHEMA='investmitra', AWS_REGION='auto', PYTHONUNBUFFERED='1',
               TZ='Asia/Kolkata')
    env.setdefault('CC_BUCKET_RAW', 'cc-raw')
    env.setdefault('CC_BUCKET_QUARANTINE', 'cc-quarantine')
    for command in commands:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Pipeline exceeded its 90-minute execution budget')
        print('Running:', ' '.join(command), flush=True)
        run([sys.executable, *command], cwd=ROOT, env=env, check=True,
            timeout=min(remaining, 1800))


def prepare(target, sessions, check_only=False):
    ready, missing = inspect(target, sessions)
    if ready:
        return
    if check_only:
        raise RuntimeError('EOD data not ready')
    if not recovery_allowed(datetime.now(IST)):
        raise RuntimeError('Rebuild cutoff reached; no automatic recovery after 06:00 IST')
    deadline = time.monotonic() + 90*60
    for missing_date in missing or [target]:
        execute(price_commands(missing_date), missing_date, deadline)
    execute(score_commands(target), target, deadline)
    inspect(target, sessions, mark=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--plan', action='store_true', help='Print commands without network or writes')
    parser.add_argument('--date', type=date.fromisoformat)
    args = parser.parse_args()
    if args.plan:
        if not args.date:
            parser.error('--plan requires --date')
        for command in price_commands(args.date) + score_commands(args.date):
            print('python', *command)
        return
    now = datetime.now(IST)
    holidays = load_nse_holidays(now.date(), validate_session=False)
    expected = completed_session(now, holidays)
    target = args.date or expected
    if target > expected or target.weekday() >= 5 or target in holidays:
        raise ValueError('Target must be a completed regular NSE session')
    os.environ['TRADE_DATE'] = str(target)
    prepare(target, recent_sessions(target, holidays), args.check_only)
    os.environ.update(READY='true', MORNING=str(args.check_only).lower())
    from pipeline_readiness_report import main as report
    if args.check_only:
        from pipeline_heartbeat import ping
        ping('PIPELINE_HEARTBEAT_SUCCESS_URL', required=True)
    report()


if __name__ == '__main__':
    main()
