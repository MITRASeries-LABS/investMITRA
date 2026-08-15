"""investMITRA — Financial Stress Score v2 (fixed ratio computation)"""
from __future__ import annotations
import argparse, io, logging, os
from datetime import date, datetime, timedelta, timezone
import boto3, pandas as pd, numpy as np
import psycopg2
import pyarrow as pa
import pyarrow.parquet as pq
from dotenv import load_dotenv
load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
IST          = timezone(timedelta(hours=5, minutes=30))
NEON_URL     = os.getenv("CC_POSTGRES_URL")
AWS_ENDPOINT = os.getenv("AWS_ENDPOINT_URL")
AWS_KEY      = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET   = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET       = os.getenv("CC_BUCKET_RAW", "cc-raw")
ENV          = os.getenv("CC_ENV", "prod")
_SECTOR_MAP: dict = {}


def get_sector_map() -> dict:
    global _SECTOR_MAP
    if _SECTOR_MAP: return _SECTOR_MAP
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("SELECT isin, COALESCE(sector,'Unknown'), market_cap_category FROM investmitra.company_master WHERE isin IS NOT NULL")
    _SECTOR_MAP = {r[0]: {"sector": r[1], "cap_cat": r[2]} for r in cur.fetchall()}
    cur.close(); conn.close()
    return _SECTOR_MAP


def load_and_compute(as_of_date: date) -> pd.DataFrame:
    """Load financials and compute all ratios in one step."""
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("""
        WITH ranked AS (
            SELECT isin, period_end, revenue_cr, ebitda_cr, ebit_cr, pat_cr,
                   total_debt_cr, cash_cr, equity_cr,
                   ROW_NUMBER() OVER (PARTITION BY isin ORDER BY period_end DESC) AS rn
            FROM investmitra.company_financials
            WHERE filing_date <= %s AND period_type = 'Q'
        )
        SELECT * FROM ranked WHERE rn <= 4
    """, (as_of_date,))
    rows = cur.fetchall()
    cur.close(); conn.close()

    df = pd.DataFrame(rows, columns=[
        "isin","period_end","revenue_cr","ebitda_cr","ebit_cr","pat_cr",
        "total_debt_cr","cash_cr","equity_cr","rn"
    ])
    logger.info("Loaded %d records for %d ISINs", len(df), df["isin"].nunique())

    # For each ISIN use best available value across 4 quarters
    def best(g, col):
        v = g.sort_values("rn")[col].dropna()
        return float(v.iloc[0]) if len(v) > 0 else np.nan

    result = {}
    for col in ["revenue_cr","ebitda_cr","ebit_cr","pat_cr","total_debt_cr","cash_cr","equity_cr"]:
        result[col] = df.groupby("isin").apply(lambda g: best(g, col))

    # Build clean DataFrame
    latest_period = df[df["rn"]==1].set_index("isin")["period_end"]
    out = pd.DataFrame(result)
    out["period_end"] = out.index.map(latest_period)
    out = out.reset_index().rename(columns={"isin": "isin"})
    # Fix: groupby returns isin as index
    out.index.name = None
    if "isin" not in out.columns:
        out = out.reset_index()

    logger.info("After best-fill: debt=%d equity=%d revenue=%d",
                out["total_debt_cr"].notna().sum(),
                out["equity_cr"].notna().sum(),
                out["revenue_cr"].notna().sum())

    # Compute ratios
    def sdiv(a, b):
        r = a / b.replace(0, np.nan)
        return r.where(b.notna() & a.notna(), np.nan)

    out["debt_equity"]     = sdiv(out["total_debt_cr"], out["equity_cr"]).clip(-10, 50)
    out["net_debt_ebitda"] = sdiv(out["total_debt_cr"].fillna(0) - out["cash_cr"].fillna(0), out["ebitda_cr"]).clip(-20, 50)
    out["pat_margin"]      = sdiv(out["pat_cr"], out["revenue_cr"]).clip(-1, 1)
    out["rev_growth_qoq"]  = np.nan  # need prev quarter — skip for now
    out["cash_ratio"]      = sdiv(out["cash_cr"], out["total_debt_cr"].replace(0, np.nan)).clip(0, 10)
    out["pat_negative"]    = (out["pat_cr"].fillna(0) < 0).astype(float) * 100

    # Add sector
    sm = get_sector_map()
    out["sector"]  = out["isin"].map(lambda x: sm.get(x, {}).get("sector", "Unknown"))
    out["cap_cat"] = out["isin"].map(lambda x: sm.get(x, {}).get("cap_cat", "MICRO"))

    logger.info("Ratios computed: debt_equity=%d pat_margin=%d",
                out["debt_equity"].notna().sum(), out["pat_margin"].notna().sum())
    return out


def compute_stress_score(df: pd.DataFrame, score_date: date) -> pd.DataFrame:
    out = df.copy()

    def prank_high(s):  # higher = more stressed
        return s.rank(pct=True, na_option='keep') * 100
    def prank_low(s):   # lower = more stressed
        return (1 - s.rank(pct=True, na_option='keep')) * 100

    out["r_debt_equity"]     = out.groupby("sector")["debt_equity"].transform(prank_high)
    out["r_net_debt_ebitda"] = out.groupby("sector")["net_debt_ebitda"].transform(prank_high)
    out["r_pat_margin"]      = out.groupby("sector")["pat_margin"].transform(prank_low)
    out["r_cash_ratio"]      = out.groupby("sector")["cash_ratio"].transform(prank_low)
    out["r_pat_negative"]    = out["pat_negative"]

    weights = {"r_debt_equity":0.30, "r_net_debt_ebitda":0.25,
               "r_pat_margin":0.25, "r_cash_ratio":0.15, "r_pat_negative":0.05}

    out["stress_raw"] = sum(out[c].fillna(50) * w for c, w in weights.items())

    out["financial_stress_score"] = out.groupby("sector")["stress_raw"].transform(
        lambda x: ((x - x.min()) / (x.max() - x.min() + 1e-10)) * 100
    ).round(2)

    out["score_date"]    = score_date
    out["score_version"] = "v2.0"

    keep = ["isin","score_date","sector","cap_cat","financial_stress_score",
            "debt_equity","net_debt_ebitda","pat_margin","cash_ratio","pat_negative",
            "revenue_cr","ebitda_cr","pat_cr","total_debt_cr","cash_cr","equity_cr",
            "period_end","score_version"]

    result = out[[c for c in keep if c in out.columns]].sort_values(
        "financial_stress_score", ascending=False).reset_index(drop=True)

    logger.info("Scores: min=%.1f max=%.1f mean=%.1f",
                result["financial_stress_score"].min(),
                result["financial_stress_score"].max(),
                result["financial_stress_score"].mean())

    top10 = result.head(10)[["isin","sector","financial_stress_score","debt_equity","pat_margin"]]
    bot10 = result.tail(10)[["isin","sector","financial_stress_score","debt_equity","pat_margin"]]
    logger.info("Most stressed:\n%s", top10.to_string())
    logger.info("Least stressed:\n%s", bot10.to_string())
    return result


def write_to_r2(df: pd.DataFrame, score_date: date) -> str:
    if df.empty: return ""
    key = (f"{ENV}/scores/financial_stress/year={score_date.year}"
           f"/month={score_date.month:02d}/financial_stress_{score_date.strftime('%Y%m%d')}.parquet")
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
    df     = load_and_compute(target)
    scores = compute_stress_score(df, target)
    path   = write_to_r2(scores, target)
    print({"date": str(target), "isins": len(scores), "path": path, "status": "ok"})


if __name__ == "__main__":
    main()