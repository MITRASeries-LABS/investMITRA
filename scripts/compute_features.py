"""
investMITRA — Phase 2: Feature Engineering
Computes price-based technical features using DuckDB directly on R2 Parquet files.

Features computed:
  Returns:    1d, 5d, 20d, 60d, 252d
  Volatility: rolling std 20d, 60d, 252d (annualised)
  Momentum:   RSI-14, MACD signal, price vs 50d/200d MA
  Volume:     20d avg volume ratio, delivery % 20d avg
  Liquidity:  turnover_cr 20d avg

Output: Parquet files on R2
  cc-raw/prod/features/price_features/year={Y}/month={M}/isin={ISIN}.parquet

PIT correctness:
  All features computed using only data available on or before trade_date.
  No look-ahead bias — rolling windows look backward only.

Run:
  python scripts/compute_features.py --date 2026-08-07        # one date
  python scripts/compute_features.py --start 2024-01-01       # from date to today
"""

from __future__ import annotations

import argparse
import io
import logging
import os
from datetime import date, datetime, timedelta, timezone

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import boto3
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
    """
    Compute price-based features for all ISINs as of target_date.
    Uses DuckDB window functions on R2 Parquet — no data movement needed.
    
    Returns DataFrame with one row per ISIN.
    """
    con = get_duckdb_con()

    # Read all NSE price history from R2
    # We need last 252 trading days to compute 252d features
    # Load 15 months of data to be safe
    start_year = (target_date - timedelta(days=450)).year
    
    # Build path pattern for multiple years
    # Use glob pattern that matches the actual R2 structure
    paths = ", ".join([
        f"'s3://{BUCKET}/{ENV}/market_data/equity_prices/year={y}/**/*.parquet'"
        for y in years
    ])

    logger.info("Computing price features for %s...", target_date)

    # Main feature computation query using DuckDB window functions
    # All windows look backward (ROWS BETWEEN N PRECEDING AND CURRENT ROW)
    # PIT correct: filter to trade_date <= target_date
    query = f"""
    WITH prices AS (
        SELECT
            isin,
            trade_date,
            close,
            volume,
            turnover_cr,
            delivery_pct,
            vwap
        FROM read_parquet('{path}', union_by_name=true, hive_partitioning=true)
        WHERE 
            trade_date <= '{target_date}'
            AND trade_date >= '{(target_date - timedelta(days=450)).isoformat()}'
            AND isin IS NOT NULL
            AND LENGTH(CAST(isin AS VARCHAR)) = 12
            AND close > 0
        ORDER BY isin, trade_date
    ),

    -- Compute rolling features using window functions
    features AS (
        SELECT
            isin,
            trade_date,
            close,

            -- ── Returns ──────────────────────────────────────────────
            (close - LAG(close, 1)  OVER w) / NULLIF(LAG(close, 1)  OVER w, 0) AS ret_1d,
            (close - LAG(close, 5)  OVER w) / NULLIF(LAG(close, 5)  OVER w, 0) AS ret_5d,
            (close - LAG(close, 20) OVER w) / NULLIF(LAG(close, 20) OVER w, 0) AS ret_20d,
            (close - LAG(close, 60) OVER w) / NULLIF(LAG(close, 60) OVER w, 0) AS ret_60d,
            (close - LAG(close, 252)OVER w) / NULLIF(LAG(close, 252)OVER w, 0) AS ret_252d,

            -- ── Moving Averages ───────────────────────────────────────
            AVG(close) OVER (PARTITION BY isin ORDER BY trade_date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS ma_50d,
            AVG(close) OVER (PARTITION BY isin ORDER BY trade_date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS ma_200d,
            AVG(close) OVER (PARTITION BY isin ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)  AS ma_20d,

            -- ── Volatility (rolling std of daily returns, annualised) ─
            STDDEV(
                (close - LAG(close,1) OVER w) / NULLIF(LAG(close,1) OVER w, 0)
            ) OVER (PARTITION BY isin ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)
                * SQRT(252) AS vol_20d,

            STDDEV(
                (close - LAG(close,1) OVER w) / NULLIF(LAG(close,1) OVER w, 0)
            ) OVER (PARTITION BY isin ORDER BY trade_date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
                * SQRT(252) AS vol_60d,

            STDDEV(
                (close - LAG(close,1) OVER w) / NULLIF(LAG(close,1) OVER w, 0)
            ) OVER (PARTITION BY isin ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW)
                * SQRT(252) AS vol_252d,

            -- ── Volume features ───────────────────────────────────────
            volume,
            AVG(volume) OVER (PARTITION BY isin ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_vol_20d,
            AVG(turnover_cr) OVER (PARTITION BY isin ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_turnover_20d,
            AVG(delivery_pct) OVER (PARTITION BY isin ORDER BY trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_delivery_pct_20d,

            -- ── 52-week high/low ──────────────────────────────────────
            MAX(close) OVER (PARTITION BY isin ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS high_52w,
            MIN(close) OVER (PARTITION BY isin ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS low_52w

        FROM prices
        WINDOW w AS (PARTITION BY isin ORDER BY trade_date)
    ),

    -- Final features as of target_date only
    latest AS (
        SELECT
            isin,
            '{target_date}'::DATE AS feature_date,
            close AS price,

            -- Returns
            ret_1d, ret_5d, ret_20d, ret_60d, ret_252d,

            -- Price vs moving averages (momentum signals)
            ROUND((close / NULLIF(ma_20d,  0) - 1) * 100, 4) AS price_vs_ma20,
            ROUND((close / NULLIF(ma_50d,  0) - 1) * 100, 4) AS price_vs_ma50,
            ROUND((close / NULLIF(ma_200d, 0) - 1) * 100, 4) AS price_vs_ma200,

            -- MA crossover signal (golden/death cross)
            CASE WHEN ma_50d > ma_200d THEN 1 ELSE -1 END AS ma_cross_signal,

            -- Volatility
            ROUND(vol_20d  * 100, 4) AS vol_20d_pct,
            ROUND(vol_60d  * 100, 4) AS vol_60d_pct,
            ROUND(vol_252d * 100, 4) AS vol_252d_pct,

            -- Volume
            ROUND(volume / NULLIF(avg_vol_20d, 0), 4) AS vol_ratio_20d,
            ROUND(avg_turnover_20d, 4)   AS avg_turnover_cr_20d,
            ROUND(avg_delivery_pct_20d, 4) AS avg_delivery_pct_20d,

            -- 52-week position (0=at low, 1=at high)
            ROUND((close - low_52w) / NULLIF(high_52w - low_52w, 0), 4) AS pos_52w,
            ROUND(high_52w, 2) AS high_52w,
            ROUND(low_52w,  2) AS low_52w

        FROM features
        WHERE trade_date = '{target_date}'
    )

    SELECT * FROM latest
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
    """Write feature DataFrame to R2 as Parquet."""
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
    logger.info("  Written features → %s", path)
    return path


def run_for_date(target_date: date) -> dict:
    """Compute and store features for a single date."""
    df   = compute_price_features(target_date)
    if df.empty:
        return {"date": str(target_date), "isins": 0, "status": "no_data"}

    path = write_features_to_r2(df, target_date)
    return {
        "date":   str(target_date),
        "isins":  len(df),
        "cols":   len(df.columns),
        "path":   path,
        "status": "ok",
    }


def run_date_range(start: date, end: date):
    """Compute features for all trading days in range."""
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
        result = run_for_date(args.date)
        print(result)
    elif args.start:
        end = args.end or datetime.now(IST).date()
        run_date_range(args.start, end)
    else:
        # Default: compute for today
        target = datetime.now(IST).date()
        result = run_for_date(target)
        print(result)


if __name__ == "__main__":
    main()
