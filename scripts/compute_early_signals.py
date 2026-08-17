"""
investMITRA — Early Signal Features v2
Volume + Price patterns only (works for 10 years of R2 data).

Signals:
  1. Volume Surge         — volume 3x above 20d avg (unusual interest)
  2. Price-Volume Diverge — volume rising, price flat (accumulation)
  3. Volatility Coil      — price range compressing (big move incoming)
  4. 52-week Breakout     — price at new highs on above-avg volume
  5. Oversold Bounce      — price at 52w low but volume recovering

These patterns find stocks 5-20 days BEFORE mainstream momentum signals.
No delivery data needed — works with 10 years of historical data.

Output: Parquet on R2
  cc-raw/prod/signals/early_signals/year={Y}/month={M}/early_signals_{YYYYMMDD}.parquet
"""
from __future__ import annotations
import argparse, io, logging, os
from datetime import date, datetime, timedelta, timezone
import boto3, duckdb, pandas as pd, numpy as np
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


def get_duckdb_con():
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


def compute_early_signals(target_date: date) -> pd.DataFrame:
    con        = get_duckdb_con()
    start_date = (target_date - timedelta(days=120)).isoformat()  # 120d for stable averages

    start_year = (target_date - timedelta(days=120)).year
    years      = list(range(start_year, target_date.year + 1))
    paths      = ", ".join(
        f"'s3://{BUCKET}/{ENV}/market_data/equity_prices/year={y}/**/*.parquet'"
        for y in years
    )

    logger.info("Computing early signals for %s...", target_date)

    query = f"""
    WITH raw AS (
        SELECT
            isin,
            trade_date::DATE   AS trade_date,
            close::DOUBLE      AS close,
            high::DOUBLE       AS high,
            low::DOUBLE        AS low,
            volume::BIGINT     AS volume,
            turnover_cr::DOUBLE AS turnover_cr
        FROM read_parquet([{paths}], union_by_name=true, hive_partitioning=true)
        WHERE trade_date >= '{start_date}'
          AND trade_date <= '{target_date}'
          AND isin IS NOT NULL
          AND LENGTH(CAST(isin AS VARCHAR)) = 12
          AND close > 0
          AND volume > 0
          AND isin LIKE 'INE%'
          AND SUBSTRING(CAST(isin AS VARCHAR), 12, 1) = '1'
    ),

    with_ret AS (
        SELECT *,
            (close - LAG(close,1) OVER w) / NULLIF(LAG(close,1) OVER w, 0) AS ret_1d,
            high - low AS daily_range
        FROM raw
        WINDOW w AS (PARTITION BY isin ORDER BY trade_date)
    ),

    features AS (
        SELECT
            isin, trade_date, close, high, low, volume, ret_1d, daily_range,

            -- Volume rolling averages
            AVG(volume) OVER (PARTITION BY isin ORDER BY trade_date
                ROWS BETWEEN 59 PRECEDING AND 6 PRECEDING)  AS avg_vol_prior,
            AVG(volume) OVER (PARTITION BY isin ORDER BY trade_date
                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)  AS avg_vol_20d,
            AVG(volume) OVER (PARTITION BY isin ORDER BY trade_date
                ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)   AS avg_vol_5d,

            -- Price rolling stats
            AVG(close) OVER (PARTITION BY isin ORDER BY trade_date
                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)  AS ma_20d,
            AVG(close) OVER (PARTITION BY isin ORDER BY trade_date
                ROWS BETWEEN 49 PRECEDING AND CURRENT ROW)  AS ma_50d,

            -- Volatility (daily range based — more stable than return-based)
            AVG(daily_range) OVER (PARTITION BY isin ORDER BY trade_date
                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)  AS avg_range_20d,
            AVG(daily_range) OVER (PARTITION BY isin ORDER BY trade_date
                ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)   AS avg_range_5d,

            -- Return volatility
            STDDEV(ret_1d) OVER (PARTITION BY isin ORDER BY trade_date
                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)  AS ret_vol_20d,
            STDDEV(ret_1d) OVER (PARTITION BY isin ORDER BY trade_date
                ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)   AS ret_vol_5d,

            -- Price returns
            (close - LAG(close,5)  OVER w) / NULLIF(LAG(close,5)  OVER w, 0) AS ret_5d,
            (close - LAG(close,20) OVER w) / NULLIF(LAG(close,20) OVER w, 0) AS ret_20d,

            -- 52-week high/low
            MAX(close) OVER (PARTITION BY isin ORDER BY trade_date
                ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS high_52w,
            MIN(close) OVER (PARTITION BY isin ORDER BY trade_date
                ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS low_52w,

            -- Turnover (liquidity filter)
            AVG(turnover_cr) OVER (PARTITION BY isin ORDER BY trade_date
                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW)  AS avg_turnover_20d

        FROM with_ret
        WINDOW w AS (PARTITION BY isin ORDER BY trade_date)
    )

    SELECT * FROM features
    WHERE trade_date = '{target_date}'
      AND avg_vol_20d > 0
      AND avg_vol_prior > 0
      AND avg_turnover_20d >= 0.1  -- min 10 lakh avg daily turnover (liquidity filter)
    ORDER BY isin
    """

    try:
        df = con.execute(query).df()
        con.close()
        logger.info("  Raw data: %d ISINs", len(df))
    except Exception as e:
        logger.error("Query failed: %s", e)
        con.close()
        return pd.DataFrame()

    if df.empty:
        return df

    # ── Signal 1: VOLUME SURGE ─────────────────────────────────────────────
    # Current 5d avg volume vs prior 60d avg — catching unusual interest
    vol_surge_ratio = (df["avg_vol_5d"] / df["avg_vol_prior"].replace(0, np.nan)).clip(0, 10)
    df["volume_surge_score"] = ((vol_surge_ratio - 1) / 4 * 100).clip(0, 100).round(2)

    # ── Signal 2: PRICE-VOLUME DIVERGENCE ─────────────────────────────────
    # Volume rising significantly but price barely moving = accumulation
    vol_rising   = (vol_surge_ratio > 1.5).astype(float)  # volume up 50%+
    price_quiet  = (df["ret_5d"].abs().fillna(0) < 0.03).astype(float)  # price move <3%
    vol_surge_strength = vol_surge_ratio.clip(0, 5) / 5  # normalized

    df["price_vol_divergence_score"] = (
        vol_rising * 40 +
        price_quiet * 30 +
        vol_surge_strength * 30
    ).clip(0, 100).round(2)

    # ── Signal 3: VOLATILITY COIL ──────────────────────────────────────────
    # Price range compressing = energy building for a big move
    range_compression = (df["avg_range_5d"] / df["avg_range_20d"].replace(0, np.nan)).clip(0, 2)
    ret_vol_compression = (df["ret_vol_5d"] / df["ret_vol_20d"].replace(0, np.nan)).clip(0, 2)

    # Low compression ratio = tight coil
    df["volatility_coil_score"] = (
        (1 - range_compression.clip(0, 1)) * 50 +
        (1 - ret_vol_compression.clip(0, 1)) * 50
    ).clip(0, 100).round(2)

    # ── Signal 4: 52-WEEK BREAKOUT ────────────────────────────────────────
    # Price near 52-week high + volume surge = genuine breakout
    pos_52w = ((df["close"] - df["low_52w"]) /
               (df["high_52w"] - df["low_52w"]).replace(0, np.nan)).clip(0, 1)
    near_high = (pos_52w > 0.90).astype(float)  # within 10% of 52w high
    at_high   = (pos_52w > 0.98).astype(float)  # at new 52w high

    df["breakout_score"] = (
        pos_52w * 40 +
        near_high * 30 +
        at_high * 15 +
        (vol_surge_ratio.clip(0, 3) / 3) * 15
    ).clip(0, 100).round(2)

    # ── Signal 5: OVERSOLD BOUNCE ─────────────────────────────────────────
    # Price near 52-week low but volume recovering = potential bottom
    at_low     = (pos_52w < 0.10).astype(float)  # within 10% of 52w low
    vol_recovering = (vol_surge_ratio > 1.2).astype(float)
    price_stabilizing = (df["ret_5d"].fillna(-1) > -0.02).astype(float)  # not still falling

    df["oversold_bounce_score"] = (
        at_low * 40 +
        vol_recovering * 35 +
        price_stabilizing * 25
    ).clip(0, 100).round(2)

    # ── Composite Early Signal Score ──────────────────────────────────────
    # Weight by predictive power for Indian markets
    df["early_signal_score"] = (
        df["price_vol_divergence_score"] * 0.35 +  # most powerful
        df["volatility_coil_score"]      * 0.25 +  # energy building
        df["volume_surge_score"]         * 0.20 +  # unusual interest
        df["breakout_score"]             * 0.15 +  # confirmed move
        df["oversold_bounce_score"]      * 0.05    # contrarian
    ).round(2)

    # Signal label
    df["early_signal"] = pd.cut(
        df["early_signal_score"],
        bins=[0, 25, 45, 60, 75, 100],
        labels=["Weak", "Neutral", "Watch", "Alert", "Strong Alert"],
        include_lowest=True
    )

    # Add 52w position for context
    df["pos_52w"] = pos_52w.round(4)

    # Keep useful columns
    keep = [
        "isin", "trade_date", "close", "volume",
        "early_signal_score", "early_signal",
        "volume_surge_score", "price_vol_divergence_score",
        "volatility_coil_score", "breakout_score", "oversold_bounce_score",
        "ret_5d", "ret_20d", "pos_52w",
        "avg_vol_5d", "avg_vol_20d", "avg_vol_prior",
        "avg_turnover_20d",
    ]

    result = df[[c for c in keep if c in df.columns]].copy()
    result["ret_5d"]  = result["ret_5d"].round(4)
    result["ret_20d"] = result["ret_20d"].round(4)
    result = result.sort_values("early_signal_score", ascending=False).reset_index(drop=True)

    alert_count = (result["early_signal_score"] >= 60).sum()
    logger.info("  Mean=%.1f | Watch+=%d | Alert+=%d | Strong Alert=%d",
                result["early_signal_score"].mean(),
                (result["early_signal_score"] >= 45).sum(),
                alert_count,
                (result["early_signal_score"] >= 75).sum())

    top = result.head(20)
    logger.info("Top 20 early signals:\n%s",
                top[["isin","early_signal_score","early_signal",
                      "volume_surge_score","price_vol_divergence_score",
                      "volatility_coil_score","breakout_score"]].to_string())

    return result


def write_to_r2(df: pd.DataFrame, target_date: date) -> str:
    if df.empty: return ""
    key = (f"{ENV}/signals/early_signals/year={target_date.year}"
           f"/month={target_date.month:02d}"
           f"/early_signals_{target_date.strftime('%Y%m%d')}.parquet")
    s3  = boto3.client("s3", endpoint_url=AWS_ENDPOINT,
                       aws_access_key_id=AWS_KEY, aws_secret_access_key=AWS_SECRET,
                       region_name="auto")
    buf = io.BytesIO()
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), buf, compression="snappy")
    buf.seek(0)
    s3.put_object(Bucket=BUCKET, Key=key, Body=buf.read())
    path = f"s3://{BUCKET}/{key}"
    logger.info("Written → %s (%d rows)", path, len(df))
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date",  type=date.fromisoformat)
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end",   type=date.fromisoformat)
    args = parser.parse_args()

    if args.start:
        current = args.start
        end     = args.end or datetime.now(IST).date()
        while current <= end:
            if current.weekday() < 5:
                df = compute_early_signals(current)
                if not df.empty:
                    write_to_r2(df, current)
            current += timedelta(days=1)
    else:
        target = args.date or datetime.now(IST).date()
        df     = compute_early_signals(target)
        if not df.empty:
            path = write_to_r2(df, target)
            print(f"\nDone: {len(df)} ISINs → {path}")
            print(f"\nTop 10 Early Signal Alerts:")
            top = df[df["early_signal_score"] >= 45].head(10)
            print(top[["isin","early_signal_score","early_signal",
                        "volume_surge_score","price_vol_divergence_score",
                        "volatility_coil_score","ret_5d"]].to_string())


if __name__ == "__main__":
    main()
