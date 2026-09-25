"""Require nonempty, correctly dated daily lake inputs before scoring.

Read only. Never fall back to an older date or relabel old scores.
"""
import argparse
from datetime import date


def validate_counts(target, total, matching, label):
    if total <= 0 or matching != total:
        raise RuntimeError(f'{label}: require nonempty data entirely dated {target}; '
                           f'total={total}, matching={matching}')


def verify(target, stage):
    # Reuse the established R2 client/configuration. Import only when running.
    from compute_features import get_duckdb_con, BUCKET, ENV
    prefix = f's3://{BUCKET}/{ENV}'
    if stage == 'prices':
        path = (f'{prefix}/market_data/equity_prices/year={target.year}'
                f'/month={target.month:02d}/day={target.day:02d}/nse_bhavcopy_*.parquet')
        column = 'trade_date'
    elif stage == 'features':
        path = (f'{prefix}/features/price_features/year={target.year}/month={target.month:02d}'
                f'/price_features_{target:%Y%m%d}.parquet')
        column = 'feature_date'
    else:
        path = (f'{prefix}/scores/momentum/year={target.year}/month={target.month:02d}'
                f'/momentum_{target:%Y%m%d}.parquet')
        column = 'score_date'
    con = get_duckdb_con()
    try:
        total, matching = con.execute(
            f'SELECT COUNT(*), COUNT(*) FILTER (WHERE CAST({column} AS DATE) = ?) '
            'FROM read_parquet(?, union_by_name=true)', [target, path]).fetchone()
        validate_counts(target, total, matching, stage)
        print(f'VALIDATED {stage}: {total} rows dated {target}')
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', required=True, type=date.fromisoformat)
    parser.add_argument('--stage', required=True, choices=['prices', 'features', 'momentum'])
    args = parser.parse_args()
    verify(args.date, args.stage)


if __name__ == '__main__':
    main()
