"""Resolve one EOD date for a workflow; never infer it separately per job.

18:00 IST is the default completed-day cutoff, not an exchange holiday calendar.
Explicit dates are preserved. Missing source data must fail downstream validation.
"""
import argparse
import os
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def resolve_trade_date(explicit=None, *, now=None):
    if explicit:
        return explicit if isinstance(explicit, date) else date.fromisoformat(explicit)
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        raise ValueError('Date resolution requires a timezone-aware timestamp')
    local = now.astimezone(IST)
    target = local.date()
    if local.hour < 18:
        target -= timedelta(days=1)
    while target.weekday() >= 5:
        target -= timedelta(days=1)
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default='')
    parser.add_argument('--start', default='')
    parser.add_argument('--end', default='')
    args = parser.parse_args()
    if args.date and (args.start or args.end):
        parser.error('Use either --date or --start/--end')
    if args.end and not args.start:
        parser.error('--end requires --start')
    target = resolve_trade_date(args.date or args.end or os.getenv('TRADE_DATE'))
    if args.start and date.fromisoformat(args.start) > target:
        parser.error('Range start is after end')
    print(target.isoformat())


if __name__ == '__main__':
    main()
