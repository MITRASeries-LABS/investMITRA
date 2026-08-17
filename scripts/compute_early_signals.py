"""
investMITRA — Early Signal Features
Finds stocks showing unusual patterns BEFORE price moves.

Signals:
  1. Delivery % Spike    — smart money accumulating quietly
  2. Volume Anomaly      — unusual volume without price move (coiling)
  3. 52-week Breakout    — price breaking out on high delivery
  4. Momentum Divergence — price flat but delivery/volume rising
  5. Low Volatility Coil — tight range = big move incoming

These patterns find stocks 5-20 days BEFORE mainstream momentum signals.

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
    """
    Compute early signal features for all ISINs as of target_date.
    Reads last 60 days of NSE price data from R2.
    """
    con        = get_duckdb_con()
    start_date = (target_date - timedelta(days=90)).isoformat()

    # Build year paths
    start_year = (target_date - timedelta(days=90)).year
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
            trade_date::DATE    AS trade_date,
            close::DOUBLE       AS close,
            volume::BIGINT      AS volume,
            delivery_pct::DOUBLE AS delivery_pct,
            turnover_cr::DOUBLE  AS turnover_cr
        FROM read_parquet([{paths}], union_by_name=true, hive_partitioning=true)
        WHERE trade_date >= '{start_date}'
          AND trade_date <= '{target_date}'
          AND isin IS NOT NULL
          AND LENGTH(CAST(isin AS VARCHAR)) = 12
          AND close > 0
          AND isin LIKE 'INE%'  -- equity ISINs only
    ),

    -- Daily returns
    with_ret AS (
        SELECT *,
            (close - LAG(close,1) OVER w) / NULLIF(LAG(close,1) OVER w, 0) AS ret_1d
        FROM raw
        WINDOW w AS (PARTITION BY isin ORDER BY trade_date)
    ),

    -- Rolling aggregates over last 5, 20 days
    signals AS (
        SELECT
            isin, trade_date, close, volume, delivery_pct, ret_1d,

            -- Delivery % features
            AVG(delivery_pct) OVER (PARTITION BY isin ORDER BY trade_date
                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_del_20d,
            AVG(delivery_pct) OVER (PARTITION BY isin ORDER BY trade_date
                ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)  AS avg_del_5d,
            AVG(delivery_pct) OVER (PARTITION BY isin ORDER BY trade_date
                ROWS BETWEEN 59 PRECEDING AND 6 PRECEDING) AS avg_del_prior,

            -- Volume features
            AVG(volume) OVER (PARTITION BY isin ORDER BY trade_date
                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS avg_vol_20d,
            AVG(volume) OVER (PARTITION BY isin ORDER BY trade_date
                ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)  AS avg_vol_5d,

            -- Price features
            AVG(close) OVER (PARTITION BY isin ORDER BY trade_date
                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma_20d,
            STDDEV(ret_1d) OVER (PARTITION BY isin ORDER BY trade_date
                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS vol_20d,
            STDDEV(ret_1d) OVER (PARTITION BY isin ORDER BY trade_date
                ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)  AS vol_5d,
            MAX(close) OVER (PARTITION BY isin ORDER BY trade_date
                ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS high_52w,
            MIN(close) OVER (PARTITION BY isin ORDER BY trade_date
                ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS low_52w,

            -- Price return over last 5 days
            (close - LAG(close,5) OVER w2) / NULLIF(LAG(close,5) OVER w2, 0) AS ret_5d,
            (close - LAG(close,20) OVER w2) / NULLIF(LAG(close,20) OVER w2, 0) AS ret_20d

        FROM with_ret
        WINDOW
            w  AS (PARTITION BY isin ORDER BY trade_date),
            w2 AS (PARTITION BY isin ORDER BY trade_date)
    )

    SELECT * FROM signals
    WHERE trade_date = '{target_date}'
    ORDER BY isin
    """

    try:
        df = con.execute(query).df()
        con.close()
        logger.info("  Raw signals: %d ISINs", len(df))
    except Exception as e:
        logger.error("Query failed: %s", e)
        con.close()
        return pd.DataFrame()

    if df.empty:
        return df

    # ── Compute Signal Scores ──────────────────────────────────────────────

    def safe_ratio(a, b):
        with np.errstate(divide='ignore', invalid='ignore'):
            r = np.where(b != 0, a / b, np.nan)
        return r

    # 1. DELIVERY SPIKE SCORE (0-100)
    # delivery_pct jumped significantly vs prior 60d average
    # High delivery + low price move = accumulation signal
    del_ratio = safe_ratio(df["avg_del_5d"], df["avg_del_prior"].replace(0, np.nan))
    df["delivery_spike"] = pd.Series(del_ratio).clip(0, 5).fillna(1)
    # Normalize to 0-100
    df["delivery_spike_score"] = ((df["delivery_spike"] - 1) / 4 * 100).clip(0, 100).round(2)

    # 2. VOLUME ANOMALY SCORE (0-100)
    # Volume surging while price is flat = coiling, smart money entering
    vol_ratio = safe_ratio(df["avg_vol_5d"], df["avg_vol_20d"].replace(0, np.nan))
    price_move = df["ret_5d"].abs().fillna(0)
    # High volume + low price move = high anomaly score
    df["vol_anomaly_ratio"] = pd.Series(vol_ratio).clip(0, 5).fillna(1)
    df["price_quiet"]       = (1 - price_move.clip(0, 0.1) / 0.1)  # 1=quiet, 0=moving
    df["volume_anomaly_score"] = (
        (df["vol_anomaly_ratio"] - 1) / 4 * 70 +  # volume surge component
        df["price_quiet"] * 30                      # price quiet component
    ).clip(0, 100).round(2)

    # 3. BREAKOUT SCORE (0-100)
    # Price near 52-week high + high delivery = genuine breakout
    pos_52w = safe_ratio(
        df["close"] - df["low_52w"],
        df["high_52w"] - df["low_52w"]
    )
    df["pos_52w"] = pd.Series(pos_52w).clip(0, 1).fillna(0)
    df["near_high"] = (df["pos_52w"] > 0.90).astype(float)  # within 10% of 52w high
    del_quality = (df["avg_del_5d"].fillna(0) / 100).clip(0, 1)
    df["breakout_score"] = (
        df["pos_52w"] * 50 +
        del_quality * 30 +
        df["near_high"] * 20
    ).clip(0, 100).round(2)

    # 4. MOMENTUM DIVERGENCE SCORE (0-100)
    # Delivery/volume rising while price is flat/falling = early accumulation
    # This is the most powerful early signal
    del_rising  = (df["avg_del_5d"].fillna(0) > df["avg_del_20d"].fillna(0)).astype(float)
    vol_rising  = (df["avg_vol_5d"].fillna(0) > df["avg_vol_20d"].fillna(0)).astype(float)
    price_lagging = (df["ret_5d"].fillna(0) < 0.02).astype(float)  # price up <2% in 5d

    df["momentum_divergence_score"] = (
        del_rising  * 40 +
        vol_rising  * 30 +
        price_lagging * 30
    ).clip(0, 100).round(2)

    # 5. VOLATILITY COIL SCORE (0-100)
    # Volatility compressing = big move incoming (direction unknown)
    # Low vol_5d vs vol_20d = coiling
    vol_compression = safe_ratio(df["vol_5d"], df["vol_20d"].replace(0, np.nan))
    df["vol_compression"] = pd.Series(vol_compression).clip(0, 2).fillna(1)
    df["volatility_coil_score"] = (
        (1 - df["vol_compression"].clip(0, 1)) * 100
    ).clip(0, 100).round(2)

    # ── Composite Early Signal Score ──────────────────────────────────────
    df["early_signal_score"] = (
        df["delivery_spike_score"]     * 0.30 +
        df["volume_anomaly_score"]     * 0.25 +
        df["momentum_divergence_score"]* 0.25 +
        df["breakout_score"]           * 0.15 +
        df["volatility_coil_score"]    * 0.05
    ).round(2)

    # Signal label
    df["early_signal"] = pd.cut(
        df["early_signal_score"],
        bins=[0, 30, 50, 70, 85, 100],
        labels=["Weak", "Neutral", "Watch", "Alert", "Strong Alert"],
        include_lowest=True
    )

    # Keep only useful columns
    keep = [
        "isin", "trade_date", "close",
        "early_signal_score", "early_signal",
        "delivery_spike_score", "volume_anomaly_score",
        "momentum_divergence_score", "breakout_score", "volatility_coil_score",
        "avg_del_5d", "avg_del_20d", "delivery_spike",
        "avg_vol_5d", "avg_vol_20d", "vol_anomaly_ratio",
        "ret_5d", "ret_20d", "pos_52w",
    ]

    result = df[[c for c in keep if c in df.columns]].copy()
    result = result.sort_values("early_signal_score", ascending=False).reset_index(drop=True)

    logger.info("  Early signals computed — mean=%.1f, Alert+=%d",
                result["early_signal_score"].mean(),
                (result["early_signal_score"] >= 70).sum())

    # Show top signals
    top = result[result["early_signal_score"] >= 70].head(20)
    if not top.empty:
        logger.info("Top early signals:\n%s",
                    top[["isin","early_signal_score","early_signal",
                          "delivery_spike_score","volume_anomaly_score",
                          "momentum_divergence_score"]].to_string())

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
                df   = compute_early_signals(current)
                if not df.empty:
                    write_to_r2(df, current)
            current += timedelta(days=1)
    else:
        target = args.date or datetime.now(IST).date()
        df     = compute_early_signals(target)
        if not df.empty:
            path = write_to_r2(df, target)
            print(f"Done: {len(df)} ISINs, path: {path}")
            # Print top 10 alerts
            top = df[df["early_signal_score"] >= 60].head(10)
            print("\nTop Early Signals:")
            print(top[["isin","early_signal_score","early_signal",
                        "delivery_spike_score","volume_anomaly_score",
                        "momentum_divergence_score"]].to_string())


if __name__ == "__main__":
    main()
