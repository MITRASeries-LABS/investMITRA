"""
investMITRA — Phase 2: Feature Engineering
Computes price-based technical features using DuckDB directly on R2 Parquet files.

Features computed:
  Returns:    1d, 5d, 20d, 60d, 252d
  Volatility: rolling std 20d, 60d, 252d (annualised)
  Momentum:   price vs 20d/50d/200d MA, MA crossover signal
  Volume:     20d avg volume ratio, delivery % 20d avg
  Liquidity:  turnover_cr 20d avg
  Position:   52-week high/low position

Output: Parquet files on R2
  cc-raw/prod/features/price_features/year={Y}/month={M}/price_features_{YYYYMMDD}.parquet

PIT correctness:
  All features use only data available on or before trade_date.
  No look-ahead bias.

Run:
  python scripts/compute_features.py --date 2026-08-07
  python scripts/compute_features.py --start 2024-01-01 --end 2026-08-07
"""

from __future__ import annotations

import argparse
import io
import logging
import os
from datetime import date, datetime, timedelta, timezone

import boto3
import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from dotenv import load_dotenv

load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

IST          = timezone(timedelta(hours=5, minutes=30))
AWS_ENDPOINT = os.getenv("AWS_ENDPOINT_URL")
AWS_KEY      = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET   = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET       = os.getenv("CC_BUCKET_RAW", "cc-raw")
ENV          = os.getenv("CC_ENV", "prod")


def get_duckdb_con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    endpoint = (AWS_ENDPOINT or "").replace("https://", "").replace("http://", "")
    use_ssl  = "true" if (AWS_ENDPOINT or "").startswith("https") else "false"
    con.execute(f"""
        SET s3_access_key_id     = '{AWS_KEY}';
        SET s3_secret_access_key = '{AWS_SECRET}';
        SET s3_endpoint          = '{endpoint}';
        SET s3_region            = 'auto';
        SET s3_use_ssl           = {use_ssl};
        SET s3_url_style         = 'path';
    """)
    return con


def compute_price_features(target_date: date) -> pd.DataFrame:
    """Compute price features for all ISINs as of target_date."""
    con = get_duckdb_con()

    # Single glob path covering all years/months/days
    path       = f"s3://{BUCKET}/{ENV}/market_data/equity_prices/**/*.parquet"
    start_date = (target_date - timedelta(days=450)).isoformat()

    logger.info("Computing price features for %s (data from %s)...", target_date, start_date)

    query = f"""
    WITH prices AS (
        SELECT
            isin,
            trade_date::DATE AS trade_date,
            close::DOUBLE    AS close,
            volume::BIGINT   AS volume,
            turnover_cr::DOUBLE  AS turnover_cr,
            delivery_pct::DOUBLE AS delivery_pct
        FROM read_parquet('{path}', union_by_name=true, hive_partitioning=true)
        WHERE
            trade_date >= '{start_date}'
            AND trade_date <= '{target_date}'
            AND isin IS NOT NULL
            AND LENGTH(CAST(isin AS VARCHAR)) = 12
            AND close IS NOT NULL
            AND close > 0
    ),

    features AS (
        SELECT
            isin,
            trade_date,
            close,
            volume,
            turnover_cr,
            delivery_pct,

            -- Returns
            (close - LAG(close,1)   OVER w) / NULLIF(LAG(close,1)   OVER w, 0) AS ret_1d,
            (close - LAG(close,5)   OVER w) / NULLIF(LAG(close,5)   OVER w, 0) AS ret_5d,
            (close - LAG(close,20)  OVER w) / NULLIF(LAG(close,20)  OVER w, 0) AS ret_20d,
            (close - LAG(close,60)  OVER w) / NULLIF(LAG(close,60)  OVER w, 0) AS ret_60d,
            (close - LAG(close,252) OVER w) / NULLIF(LAG(close,252) OVER w, 0) AS ret_252d,

            -- Moving averages
            AVG(close) OVER (PARTITION BY isin ORDER BY trade_date ROWS BETWEEN 19  PRECEDING AND CURRENT ROW) AS ma_20d,
            AVG(close) OVER (PARTITION BY isin ORDER BY trade_date ROWS BETWEEN 49  PRECEDING AND CURRENT ROW) AS ma_50d,
            AVG(close) OVER (PARTITION BY isin ORDER BY trade_date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS ma_200d,

            -- Volatility (annualised)
            STDDEV((close - LAG(close,1) OVER w) / NULLIF(LAG(close,1) OVER w,0))
                OVER (PARTITION BY isin ORDER BY trade_date ROWS BETWEEN 19  PRECEDING AND CURRENT ROW) * SQRT(252) AS vol_20d,
            STDDEV((close - LAG(close,1) OVER w) / NULLIF(LAG(close,1) OVER w,0))
                OVER (PARTITION BY isin ORDER BY trade_date ROWS BETWEEN 59  PRECEDING AND CURRENT ROW) * SQRT(252) AS vol_60d,
            STDDEV((close - LAG(close,1) OVER w) / NULLIF(LAG(close,1) OVER w,0))
                OVER (PARTITION BY isin ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) * SQRT(252) AS vol_252d,

            -- Volume
            AVG(volume)       OVER (PARTITION BY isin ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_vol_20d,
            AVG(turnover_cr)  OVER (PARTITION BY isin ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_turnover_20d,
            AVG(delivery_pct) OVER (PARTITION BY isin ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_delivery_pct_20d,

            -- 52-week high/low
            MAX(close) OVER (PARTITION BY isin ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS high_52w,
            MIN(close) OVER (PARTITION BY isin ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS low_52w

        FROM prices
        WINDOW w AS (PARTITION BY isin ORDER BY trade_date)
    )

    SELECT
        isin,
        '{target_date}'::DATE AS feature_date,
        close                 AS price,

        ROUND(ret_1d   * 100, 4) AS ret_1d_pct,
        ROUND(ret_5d   * 100, 4) AS ret_5d_pct,
        ROUND(ret_20d  * 100, 4) AS ret_20d_pct,
        ROUND(ret_60d  * 100, 4) AS ret_60d_pct,
        ROUND(ret_252d * 100, 4) AS ret_252d_pct,

        ROUND((close / NULLIF(ma_20d,  0) - 1) * 100, 4) AS price_vs_ma20,
        ROUND((close / NULLIF(ma_50d,  0) - 1) * 100, 4) AS price_vs_ma50,
        ROUND((close / NULLIF(ma_200d, 0) - 1) * 100, 4) AS price_vs_ma200,

        CASE WHEN ma_50d > ma_200d THEN 1 ELSE -1 END AS ma_cross_signal,

        ROUND(vol_20d  * 100, 4) AS vol_20d_pct,
        ROUND(vol_60d  * 100, 4) AS vol_60d_pct,
        ROUND(vol_252d * 100, 4) AS vol_252d_pct,

        ROUND(volume / NULLIF(avg_vol_20d, 0), 4)  AS vol_ratio_20d,
        ROUND(avg_turnover_20d, 4)                  AS avg_turnover_cr_20d,
        ROUND(avg_delivery_pct_20d, 4)              AS avg_delivery_pct_20d,

        ROUND((close - low_52w) / NULLIF(high_52w - low_52w, 0), 4) AS pos_52w,
        ROUND(high_52w, 2) AS high_52w,
        ROUND(low_52w,  2) AS low_52w

    FROM features
    WHERE trade_date = '{target_date}'
    ORDER BY isin
    """

    try:
        df = con.execute(query).df()
        logger.info("  Computed %d features for %d ISINs", len(df.columns), len(df))
        con.close()
        return df
    except Exception as e:
        logger.error("Feature computation failed: %s", e)
        con.close()
        return pd.DataFrame()


def write_features_to_r2(df: pd.DataFrame, target_date: date) -> str:
    if df.empty:
        return ""

    key = (
        f"{ENV}/features/price_features"
        f"/year={target_date.year}/month={target_date.month:02d}"
        f"/price_features_{target_date.strftime('%Y%m%d')}.parquet"
    )

    s3 = boto3.client("s3",
        endpoint_url=AWS_ENDPOINT,
        aws_access_key_id=AWS_KEY,
        aws_secret_access_key=AWS_SECRET,
        region_name="auto")

    table = pa.Table.from_pandas(df, preserve_index=False)
    buf   = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    buf.seek(0)
    s3.put_object(Bucket=BUCKET, Key=key, Body=buf.read())

    path = f"s3://{BUCKET}/{key}"
    logger.info("  Written → %s (%d rows)", path, len(df))
    return path


def run_for_date(target_date: date) -> dict:
    df = compute_price_features(target_date)
    if df.empty:
        return {"date": str(target_date), "isins": 0, "status": "no_data"}
    path = write_features_to_r2(df, target_date)
    return {"date": str(target_date), "isins": len(df), "cols": len(df.columns),
            "path": path, "status": "ok"}


def run_date_range(start: date, end: date):
    current = start
    total   = 0
    while current <= end:
        if current.weekday() < 5:
            result = run_for_date(current)
            logger.info("%s: %s", current, result)
            total += 1
        current += timedelta(days=1)
    logger.info("Done: %d dates processed", total)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date",  type=date.fromisoformat)
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end",   type=date.fromisoformat)
    args = parser.parse_args()

    if args.date:
        print(run_for_date(args.date))
    elif args.start:
        run_date_range(args.start, args.end or datetime.now(IST).date())
    else:
        print(run_for_date(datetime.now(IST).date()))


if __name__ == "__main__":
    main()
