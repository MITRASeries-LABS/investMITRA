"""Overnight data readiness; no broker access and no freshness bypass.

A receipt is written only after the chained pipeline succeeds. It binds the
validated lake objects to the date, so a same-date stale score is not sufficient.
"""
import argparse
from datetime import date, datetime, timedelta
import json
import os

from pipeline_date import IST, resolve_trade_date


def completed_session(now, holidays):
    target = resolve_trade_date(now=now)
    while True:
        if target.year not in {d.year for d in holidays}:
            raise ValueError(f'NSE calendar unavailable for {target.year}')
        if target.weekday() < 5 and target not in holidays:
            return target
        target -= timedelta(days=1)


def recent_sessions(target, holidays, count=5):
    result = []
    while len(result) < count:
        if target.year not in {d.year for d in holidays}:
            raise ValueError(f'NSE calendar unavailable for {target.year}')
        if target.weekday() < 5 and target not in holidays:
            result.append(target)
        target -= timedelta(days=1)
    return sorted(result)


def recovery_allowed(now):
    # Delayed scheduled runs must not start a long rebuild near market open.
    hour = now.astimezone(IST).hour
    return hour >= 18 or hour < 6


def lake_client():
    import boto3
    from botocore.config import Config
    return boto3.client('s3', endpoint_url=os.environ['AWS_ENDPOINT_URL'],
                        aws_access_key_id=os.environ['AWS_ACCESS_KEY_ID'],
                        aws_secret_access_key=os.environ['AWS_SECRET_ACCESS_KEY'],
                        region_name='auto', config=Config(connect_timeout=10, read_timeout=20,
                                                        retries={'max_attempts': 2}))


def lake_fingerprint(client, bucket, env, target):
    month = f'year={target.year}/month={target.month:02d}'
    prefixes = [f'{env}/market_data/equity_prices/{month}/day={target.day:02d}/',
                f'{env}/features/price_features/{month}/price_features_{target:%Y%m%d}.parquet',
                f'{env}/scores/momentum/{month}/momentum_{target:%Y%m%d}.parquet',
                f'{env}/scores/investmitra_score/{month}/investmitra_score_{target:%Y%m%d}.parquet']
    objects = {}
    for prefix in prefixes:
        found = []
        for page in client.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=prefix):
            found.extend(o for o in page.get('Contents', []) if o['Key'].endswith('.parquet'))
        if not found:
            raise RuntimeError(f'Missing pipeline output: {prefix}')
        for obj in found:
            objects[obj['Key']] = obj['ETag']
    return objects


def read_receipt(client, bucket, key):
    from botocore.exceptions import ClientError
    try:
        return json.loads(client.get_object(Bucket=bucket, Key=key)['Body'].read())
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') in ('NoSuchKey', '404'):
            return None
        raise


def receipt_matches(receipt, target, fingerprint, counts):
    return bool(receipt and receipt.get('version') == 1
                and receipt.get('date') == target.isoformat()
                and receipt.get('objects') == fingerprint
                and receipt.get('counts') == counts)


def database_counts(target, sessions):
    import psycopg2
    conn = psycopg2.connect(os.environ['CC_POSTGRES_URL'], connect_timeout=10,
                            options='-c statement_timeout=15000')
    try:
        conn.set_session(readonly=True)
        with conn.cursor() as cur:
            cur.execute('SELECT trade_date, COUNT(*) FROM investmitra.equity_prices '
                        "WHERE trade_date = ANY(%s) AND source = 'NSE' GROUP BY trade_date", (sessions,))
            prices = dict(cur.fetchall())
            cur.execute('SELECT COUNT(*) FROM investmitra.daily_scores WHERE score_date = %s '
                        'AND investmitra_score IS NOT NULL', (target,))
            scores = cur.fetchone()[0]
            cur.execute('SELECT COUNT(*) FROM investmitra.equity_prices WHERE trade_date >= %s '
                        'AND trade_date <= %s', (target-timedelta(days=29), target))
            history = cur.fetchone()[0]
        missing = [d for d in sessions if prices.get(d, 0) <= 0]
        return {'prices': prices.get(target, 0), 'scores': scores, 'history': history}, missing
    finally:
        conn.close()


def inspect(target, sessions, mark=False):
    from verify_pipeline_data import verify
    client = lake_client()
    bucket, env = os.getenv('CC_BUCKET_RAW', 'cc-raw'), os.getenv('CC_ENV', 'prod')
    key = f'{env}/pipeline_readiness/{target.isoformat()}.json'
    counts, missing = database_counts(target, sessions)
    if missing or counts['scores'] <= 0 or counts['history'] <= 10000:
        if mark:
            raise RuntimeError('Cannot mark ready: missing daily prices, scores or history')
        return False, missing
    # Validate actual date columns, not just filenames or database max dates.
    try:
        for stage in ('prices', 'features', 'momentum', 'composite'):
            verify(target, stage)
        fingerprint = lake_fingerprint(client, bucket, env, target)
    except Exception:
        if mark:
            raise
        return False, missing
    if mark:
        receipt = dict(version=1, date=target.isoformat(), objects=fingerprint, counts=counts,
                       validated_at=datetime.now(IST).isoformat(),
                       run_id=os.getenv('GITHUB_RUN_ID'), commit=os.getenv('GITHUB_SHA'))
        client.put_object(Bucket=bucket, Key=key, Body=json.dumps(receipt).encode('utf-8'),
                          ContentType='application/json')
        return True, []
    return receipt_matches(read_receipt(client, bucket, key), target, fingerprint, counts), missing


def output(values):
    path = os.getenv('GITHUB_OUTPUT')
    if path:
        with open(path, 'a', encoding='utf-8') as handle:
            for key, value in values.items():
                handle.write(f'{key}={value}\n')
    print(json.dumps(values))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', type=date.fromisoformat)
    parser.add_argument('--check-cutoff', action='store_true')
    parser.add_argument('--mark-ready', action='store_true')
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    now = datetime.now(IST)
    if args.check_cutoff:
        if not recovery_allowed(now):
            raise RuntimeError('Automatic rebuild cutoff reached (06:00 IST)')
        return
    from signal_runtime import load_nse_holidays
    holidays = load_nse_holidays(now.date(), validate_session=False)
    target = args.date or completed_session(now, holidays)
    if target > completed_session(now, holidays) or target.weekday() >= 5 or target in holidays:
        raise ValueError('Requested date is not a completed regular NSE session')
    sessions = recent_sessions(target, holidays)
    ready, missing = inspect(target, sessions, mark=args.mark_ready)
    recover = not ready and not args.check_only and recovery_allowed(now)
    output({'date': target.isoformat(), 'ready': str(ready).lower(),
            'recover': str(recover).lower(),
            'dates': json.dumps([d.isoformat() for d in (missing or [target])])})
    if not ready and not recover:
        raise RuntimeError('DATA NOT READY; automated rebuild cutoff reached or check-only requested')


if __name__ == '__main__':
    main()
