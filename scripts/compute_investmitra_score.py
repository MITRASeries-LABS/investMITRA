"""
investMITRA — Composite Score (investMITRA Score) v2
Combines four signals into one 0-100 investMITRA score.

Components:
  - Momentum Score      (25%) — price momentum, trend, volume
  - Financial Health    (35%) — inverse of Financial Stress Score
  - Management Quality  (25%) — promoter holding, institutional confidence
  - Value Quality       (15%) — Piotroski F-Score + Benjamin Graham criteria

Higher score = Better investment candidate

Output: Parquet on R2
  cc-raw/prod/scores/investmitra_score/year={Y}/month={M}/investmitra_score_{YYYYMMDD}.parquet
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
NEON_URL     = os.getenv("CC_POSTGRES_URL")


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


def load_score(con, score_type: str, score_date: date) -> pd.DataFrame | None:
    """Load most recent score file on or before score_date."""
    for days_back in range(8):
        check_date = score_date - timedelta(days=days_back)
        path = (f"s3://{BUCKET}/{ENV}/scores/{score_type}"
                f"/year={check_date.year}/month={check_date.month:02d}"
                f"/{score_type}_{check_date.strftime('%Y%m%d')}.parquet")
        try:
            df = con.execute(f"SELECT * FROM read_parquet('{path}')").df()
            logger.info("Loaded %s from %s: %d rows", score_type, check_date, len(df))
            return df
        except:
            continue
    logger.warning("Could not find %s within 7 days of %s", score_type, score_date)
    return None


def load_value_quality() -> pd.DataFrame:
    """Load Piotroski + Graham scores from Neon."""
    if not NEON_URL:
        logger.warning("CC_POSTGRES_URL not set — skipping value quality")
        return pd.DataFrame()
    try:
        import psycopg2
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        df   = pd.read_sql(
            "SELECT isin, value_quality_score, piotroski_score, graham_criteria_met "
            "FROM investmitra.value_quality",
            conn
        )
        conn.close()
        logger.info("Loaded value_quality: %d rows", len(df))
        return df
    except Exception as e:
        logger.warning("Could not load value_quality: %s", e)
        return pd.DataFrame()


def compute_investmitra_score(score_date: date) -> pd.DataFrame:
    con = get_duckdb_con()

    # Load component scores
    momentum = load_score(con, "momentum", score_date)
    stress   = load_score(con, "financial_stress", score_date)
    mgmt     = load_score(con, "management_quality", score_date)
    con.close()

    if momentum is None:
        logger.error("Momentum score missing for %s — cannot compute composite", score_date)
        return pd.DataFrame()

    # Start with momentum as base
    df = momentum[["isin", "sector", "momentum_score", "price",
                   "ret_252d_pct", "vol_20d_pct", "pos_52w"]].copy()

    # Join financial health (inverse of stress)
    if stress is not None:
        stress_cols = stress[["isin", "financial_stress_score",
                               "debt_equity", "pat_margin"]].copy()
        stress_cols["financial_health_score"] = 100 - stress_cols["financial_stress_score"]
        df = df.merge(stress_cols, on="isin", how="left")
    else:
        df["financial_stress_score"] = np.nan
        df["financial_health_score"] = 50.0
        df["debt_equity"]  = np.nan
        df["pat_margin"]   = np.nan

    # Join management quality
    if mgmt is not None:
        mgmt_cols = mgmt[["isin", "management_quality_score",
                           "insider_pct", "institution_pct"]].copy()
        df = df.merge(mgmt_cols, on="isin", how="left")
    else:
        df["management_quality_score"] = 50.0
        df["insider_pct"]     = np.nan
        df["institution_pct"] = np.nan

    # Join value quality (Piotroski + Graham)
    vq = load_value_quality()
    if not vq.empty:
        df = df.merge(
            vq[["isin", "value_quality_score", "piotroski_score", "graham_criteria_met"]],
            on="isin", how="left"
        )
        df["value_quality_score"] = df["value_quality_score"].fillna(50.0)
    else:
        df["value_quality_score"]   = 50.0
        df["piotroski_score"]       = np.nan
        df["graham_criteria_met"]   = np.nan

    # Fill missing component scores with neutral 50
    df["momentum_score"]          = df["momentum_score"].fillna(50)
    df["financial_health_score"]  = df["financial_health_score"].fillna(50)
    df["management_quality_score"]= df["management_quality_score"].fillna(50)
    df["value_quality_score"]     = df["value_quality_score"].fillna(50)

    # ── Weighted composite score v2 ─────────────────────────────────────
    # Backtested optimal weights (261,388 obs, IC=0.064):
    #   Momentum        25% — price trend signal
    #   Financial Health 35% — debt, margins, cash
    #   Management Quality 25% — promoter + institutional
    #   Value Quality    15% — Piotroski F-Score + Graham criteria
    df["investmitra_score"] = (
        df["momentum_score"]           * 0.25 +
        df["financial_health_score"]   * 0.35 +
        df["management_quality_score"] * 0.25 +
        df["value_quality_score"]      * 0.15
    ).round(2)

    # Signal labels
    df["signal"] = pd.cut(
        df["investmitra_score"],
        bins=[0, 20, 40, 60, 80, 100],
        labels=["Strong Sell", "Sell", "Neutral", "Buy", "Strong Buy"],
        include_lowest=True
    )

    df["score_date"]    = score_date
    df["score_version"] = "v2.0"

    df = df.sort_values("investmitra_score", ascending=False).reset_index(drop=True)

    logger.info("investMITRA Scores v2: min=%.1f max=%.1f mean=%.1f",
                df["investmitra_score"].min(),
                df["investmitra_score"].max(),
                df["investmitra_score"].mean())

    sig_dist = df["signal"].value_counts()
    logger.info("Signal distribution:\n%s", sig_dist.to_string())

    logger.info("\nTop 20 investMITRA picks:\n%s",
                df.head(20)[["isin","sector","investmitra_score","signal",
                              "momentum_score","financial_health_score",
                              "management_quality_score","value_quality_score",
                              "piotroski_score"]].to_string())

    return df


def write_to_r2(df: pd.DataFrame, score_date: date) -> str:
    if df.empty: return ""
    key = (f"{ENV}/scores/investmitra_score/year={score_date.year}"
           f"/month={score_date.month:02d}/investmitra_score_{score_date.strftime('%Y%m%d')}.parquet")
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
    parser.add_argument("--date", type=date.fromisoformat)
    args   = parser.parse_args()
    target = args.date or datetime.now(IST).date()

    df = compute_investmitra_score(target)
    if df.empty:
        logger.error("No scores computed")
        return

    path = write_to_r2(df, target)
    print({"date": str(target), "isins": len(df), "path": path, "status": "ok"})


if __name__ == "__main__":
    main()