"""
investMITRA — Momentum Score (Score 1)
Computes a 0-100 momentum score for each ISIN based on price features.

Methodology:
  1. Read pre-computed price features from R2
  2. For each feature, rank stocks within their sector (percentile rank 0-100)
  3. Weighted average of feature ranks = raw momentum score
  4. Normalize to 0-100

Feature weights:
  ret_252d_pct     : 25%  (1-year momentum — strongest predictor)
  ret_60d_pct      : 20%  (3-month momentum)
  ret_20d_pct      : 15%  (1-month momentum)
  pos_52w          : 15%  (52-week position)
  ma_cross_signal  : 10%  (golden/death cross)
  vol_ratio_20d    : 10%  (volume confirmation)
  ret_5d_pct       :  5%  (short-term momentum)

Output: Parquet on R2
  cc-raw/prod/scores/momentum/year={Y}/month={M}/momentum_{YYYYMMDD}.parquet

Run:
  python scripts/compute_momentum_score.py --date 2026-08-13
  python scripts/compute_momentum_score.py --start 2026-01-01 --end 2026-08-13
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
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import psycopg2
from dotenv import load_dotenv

load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

IST          = timezone(timedelta(hours=5, minutes=30))
AWS_ENDPOINT = os.getenv("AWS_ENDPOINT_URL")
AWS_KEY      = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET   = os.getenv("AWS_SECRET_ACCESS_KEY")
NEON_URL     = os.getenv("CC_POSTGRES_URL")
BUCKET       = os.getenv("CC_BUCKET_RAW", "cc-raw")
ENV          = os.getenv("CC_ENV", "prod")

# Feature weights for momentum score
WEIGHTS = {
    "ret_252d_pct":    0.25,
    "ret_60d_pct":     0.20,
    "ret_20d_pct":     0.15,
    "pos_52w":         0.15,
    "ma_cross_signal": 0.10,
    "vol_ratio_20d":   0.10,
    "ret_5d_pct":      0.05,
}

_ISIN_TO_SECTOR: dict[str, str] = {}


def get_isin_to_sector() -> dict[str, str]:
    """Load ISIN -> sector mapping from company_master."""
    global _ISIN_TO_SECTOR
    if _ISIN_TO_SECTOR:
        return _ISIN_TO_SECTOR
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute(
        "SELECT isin, COALESCE(sector, 'Unknown') FROM investmitra.company_master "
        "WHERE isin IS NOT NULL"
    )
    _ISIN_TO_SECTOR = {r[0]: r[1] for r in cur.fetchall()}
    cur.close(); conn.close()
    logger.info("Loaded %d ISIN->sector mappings", len(_ISIN_TO_SECTOR))
    return _ISIN_TO_SECTOR


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


def load_features(target_date: date) -> pd.DataFrame | None:
    """Load pre-computed price features for target_date from R2."""
    path = (f"s3://{BUCKET}/{ENV}/features/price_features"
            f"/year={target_date.year}/month={target_date.month:02d}"
            f"/price_features_{target_date.strftime('%Y%m%d')}.parquet")
    try:
        con = get_duckdb_con()
        df  = con.execute(f"SELECT * FROM read_parquet('{path}')").df()
        con.close()
        logger.info("Loaded %d ISINs from features", len(df))
        return df if not df.empty else None
    except Exception as e:
        logger.warning("No features for %s: %s", target_date, e)
        return None


def percentile_rank(series: pd.Series) -> pd.Series:
    """Convert values to percentile ranks 0-100. Higher = better."""
    return series.rank(pct=True, na_option='keep') * 100


def compute_momentum_score(df: pd.DataFrame, target_date: date) -> pd.DataFrame:
    """
    Compute momentum score for each ISIN.
    
    Steps:
    1. Add sector from company_master
    2. Rank each feature within sector (percentile rank)
    3. Weighted average of ranks = raw score
    4. Normalize within sector to 0-100
    """
    # Add sector
    sector_map = get_isin_to_sector()
    df = df.copy()
    df["sector"] = df["isin"].map(sector_map).fillna("Unknown")

    # Handle ma_cross_signal: convert -1/1 to 0/100
    if "ma_cross_signal" in df.columns:
        df["ma_cross_signal_pct"] = df["ma_cross_signal"].map({1: 100, -1: 0})
    else:
        df["ma_cross_signal_pct"] = 50

    # Cap vol_ratio_20d at 5x to avoid outlier dominance
    if "vol_ratio_20d" in df.columns:
        df["vol_ratio_20d"] = df["vol_ratio_20d"].clip(0, 5)

    # Rank each feature within sector
    feature_cols = {
        "ret_252d_pct":      WEIGHTS["ret_252d_pct"],
        "ret_60d_pct":       WEIGHTS["ret_60d_pct"],
        "ret_20d_pct":       WEIGHTS["ret_20d_pct"],
        "pos_52w":           WEIGHTS["pos_52w"],
        "ma_cross_signal_pct": WEIGHTS["ma_cross_signal"],
        "vol_ratio_20d":     WEIGHTS["vol_ratio_20d"],
        "ret_5d_pct":        WEIGHTS["ret_5d_pct"],
    }

    rank_cols = {}
    for col, weight in feature_cols.items():
        if col in df.columns:
            rank_col = f"rank_{col}"
            # Rank within sector
            df[rank_col] = df.groupby("sector")[col].transform(percentile_rank)
            rank_cols[rank_col] = weight

    if not rank_cols:
        logger.warning("No feature columns found")
        return pd.DataFrame()

    # Weighted average of ranks
    total_weight = sum(rank_cols.values())
    df["momentum_raw"] = sum(
        df[col] * (w / total_weight)
        for col, w in rank_cols.items()
    )

    # Normalize within sector to 0-100
    df["momentum_score"] = df.groupby("sector")["momentum_raw"].transform(
        lambda x: ((x - x.min()) / (x.max() - x.min() + 1e-10)) * 100
    ).round(2)

    # Add metadata
    df["score_date"]     = target_date
    df["score_version"]  = "v1.0"

    # Select output columns
    keep = [
        "isin", "score_date", "sector", "price",
        "momentum_score",
        "ret_1d_pct", "ret_5d_pct", "ret_20d_pct", "ret_60d_pct", "ret_252d_pct",
        "pos_52w", "vol_ratio_20d", "vol_20d_pct",
        "price_vs_ma50", "price_vs_ma200", "ma_cross_signal",
        "score_version",
    ]

    result = df[[c for c in keep if c in df.columns]].copy()
    result = result.sort_values("momentum_score", ascending=False).reset_index(drop=True)

    logger.info("Computed momentum scores: min=%.1f max=%.1f mean=%.1f",
                result["momentum_score"].min(),
                result["momentum_score"].max(),
                result["momentum_score"].mean())

    return result


def write_score_to_r2(df: pd.DataFrame, target_date: date) -> str:
    if df.empty: return ""
    key = (f"{ENV}/scores/momentum"
           f"/year={target_date.year}/month={target_date.month:02d}"
           f"/momentum_{target_date.strftime('%Y%m%d')}.parquet")
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


def score_exists(target_date: date) -> bool:
    s3 = boto3.client("s3", endpoint_url=AWS_ENDPOINT,
                      aws_access_key_id=AWS_KEY, aws_secret_access_key=AWS_SECRET,
                      region_name="auto")
    prefix = (f"{ENV}/scores/momentum/year={target_date.year}"
              f"/month={target_date.month:02d}/momentum_{target_date.strftime('%Y%m%d')}.parquet")
    return len(s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix).get("Contents", [])) > 0


def run_for_date(target_date: date) -> dict:
    if score_exists(target_date):
        logger.info("%s already scored — skipping", target_date)
        return {"date": str(target_date), "status": "skipped"}

    features = load_features(target_date)
    if features is None:
        return {"date": str(target_date), "status": "no_features"}

    scores = compute_momentum_score(features, target_date)
    if scores.empty:
        return {"date": str(target_date), "status": "no_scores"}

    path = write_score_to_r2(scores, target_date)

    # Print top 10 for verification
    top10 = scores.head(10)[["isin", "sector", "momentum_score", "ret_252d_pct", "pos_52w"]]
    logger.info("Top 10 momentum:\n%s", top10.to_string())

    return {
        "date":   str(target_date),
        "isins":  len(scores),
        "top":    scores.iloc[0]["isin"],
        "path":   path,
        "status": "ok",
    }


def run_date_range(start: date, end: date):
    current = start; total = 0
    while current <= end:
        if current.weekday() < 5:
            result = run_for_date(current)
            logger.info("%s: %s", current, result)
            total += 1
        current += timedelta(days=1)
    logger.info("Done: %d dates scored", total)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date",  type=date.fromisoformat)
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end",   type=date.fromisoformat)
    args = parser.parse_args()

    if args.date:    print(run_for_date(args.date))
    elif args.start: run_date_range(args.start, args.end or datetime.now(IST).date())
    else:            print(run_for_date(datetime.now(IST).date()))


if __name__ == "__main__":
    main()
