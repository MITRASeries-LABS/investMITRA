"""
investMITRA — Financial Stress Score (Score 2)
Computes a 0-100 financial stress score for each ISIN.
Higher score = MORE stressed (worse financial health).

Inputs (from company_financials via yfinance):
  - Debt/Equity ratio
  - Interest coverage (EBIT / implied interest)
  - Net debt position (Total Debt - Cash)
  - Revenue trend (QoQ growth)
  - PAT margin (PAT / Revenue)
  - Cash runway

Methodology:
  1. Compute financial ratios from latest 2 quarters
  2. Rank within sector (percentile 0-100)
  3. High rank = high stress
  4. Weighted average = raw stress score
  5. Normalize 0-100

Output: Parquet on R2
  cc-raw/prod/scores/financial_stress/year={Y}/month={M}/financial_stress_{YYYYMMDD}.parquet
"""
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

_SECTOR_MAP: dict[str, str] = {}


def get_sector_map() -> dict[str, str]:
    global _SECTOR_MAP
    if _SECTOR_MAP: return _SECTOR_MAP
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("SELECT isin, COALESCE(sector,'Unknown'), market_cap_category FROM investmitra.company_master WHERE isin IS NOT NULL")
    _SECTOR_MAP = {r[0]: {"sector": r[1], "cap_cat": r[2]} for r in cur.fetchall()}
    cur.close(); conn.close()
    logger.info("Loaded %d sector mappings", len(_SECTOR_MAP))
    return _SECTOR_MAP


def load_financials(as_of_date: date) -> pd.DataFrame:
    """
    Load latest 2 quarters of financials per ISIN as of as_of_date.
    PIT correct — only use data filed before as_of_date.
    """
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()

    cur.execute("""
        WITH ranked AS (
            SELECT
                isin, period_end, period_type,
                revenue_cr, ebitda_cr, ebit_cr, pat_cr,
                total_debt_cr, cash_cr, equity_cr,
                ROW_NUMBER() OVER (PARTITION BY isin ORDER BY period_end DESC) AS rn
            FROM investmitra.company_financials
            WHERE filing_date <= %s
              AND period_type = 'Q'
        )
        SELECT isin, period_end, revenue_cr, ebitda_cr, ebit_cr, pat_cr,
               total_debt_cr, cash_cr, equity_cr, rn
        FROM ranked WHERE rn <= 2
        ORDER BY isin, rn
    """, (as_of_date,))

    rows = cur.fetchall()
    cur.close(); conn.close()

    df = pd.DataFrame(rows, columns=[
        "isin", "period_end", "revenue_cr", "ebitda_cr", "ebit_cr", "pat_cr",
        "total_debt_cr", "cash_cr", "equity_cr", "rn"
    ])
    logger.info("Loaded %d financial records for %d ISINs", len(df), df["isin"].nunique())
    return df


def compute_ratios(df: pd.DataFrame) -> pd.DataFrame:
    """Compute financial stress ratios from latest quarter data."""

    # Latest quarter (rn=1)
    latest = df[df["rn"] == 1].copy()
    # Previous quarter (rn=2)
    prev   = df[df["rn"] == 2].copy().rename(columns={
        "revenue_cr": "rev_prev", "pat_cr": "pat_prev"
    })[["isin", "rev_prev", "pat_prev"]]

    # Merge
    result = latest.merge(prev, on="isin", how="left")

    def safe_div(a, b, default=None):
        try:
            mask = (pd.notna(a)) & (pd.notna(b)) & (b != 0)
            out  = pd.Series(index=a.index, dtype=float)
            out[mask]  = a[mask] / b[mask]
            out[~mask] = default
            return out
        except:
            return pd.Series(default, index=a.index)

    # 1. Debt/Equity (higher = more stressed)
    result["debt_equity"] = safe_div(result["total_debt_cr"], result["equity_cr"], default=np.nan)

    # 2. Net Debt / EBITDA (higher = more stressed, negative = cash-rich)
    net_debt = result["total_debt_cr"].fillna(0) - result["cash_cr"].fillna(0)
    result["net_debt_ebitda"] = safe_div(net_debt, result["ebitda_cr"], default=np.nan)

    # 3. PAT margin (lower = more stressed)
    result["pat_margin"] = safe_div(result["pat_cr"], result["revenue_cr"], default=np.nan)

    # 4. Revenue growth QoQ (lower/negative = more stressed)
    result["rev_growth_qoq"] = safe_div(
        result["revenue_cr"] - result["rev_prev"], result["rev_prev"], default=np.nan
    )

    # 5. Cash ratio (lower = more stressed)
    result["cash_ratio"] = safe_div(result["cash_cr"], result["total_debt_cr"], default=np.nan)

    # 6. Is PAT negative? (binary stress flag)
    result["pat_negative"] = (result["pat_cr"].fillna(0) < 0).astype(int)

    # Add sector
    sector_map = get_sector_map()
    result["sector"]  = result["isin"].map(lambda x: sector_map.get(x, {}).get("sector", "Unknown"))
    result["cap_cat"] = result["isin"].map(lambda x: sector_map.get(x, {}).get("cap_cat", "MICRO"))

    return result


def percentile_rank_stress(series: pd.Series) -> pd.Series:
    """Higher value = higher stress = higher rank (0-100)."""
    return series.rank(pct=True, na_option='keep') * 100


def percentile_rank_health(series: pd.Series) -> pd.Series:
    """Higher value = better health = LOWER stress rank."""
    return (1 - series.rank(pct=True, na_option='keep')) * 100


def compute_stress_score(ratios: pd.DataFrame, score_date: date) -> pd.DataFrame:
    """Compute financial stress score 0-100 (higher = more stressed)."""

    df = ratios.copy()

    # Cap extreme values
    df["debt_equity"]     = df["debt_equity"].clip(-10, 50)
    df["net_debt_ebitda"] = df["net_debt_ebitda"].clip(-20, 50)
    df["pat_margin"]      = df["pat_margin"].clip(-1, 1)
    df["rev_growth_qoq"]  = df["rev_growth_qoq"].clip(-1, 2)
    df["cash_ratio"]      = df["cash_ratio"].clip(0, 10)

    # Rank within sector
    # Stress factors (higher raw = higher stress)
    df["rank_debt_equity"]     = df.groupby("sector")["debt_equity"].transform(percentile_rank_stress)
    df["rank_net_debt_ebitda"] = df.groupby("sector")["net_debt_ebitda"].transform(percentile_rank_stress)
    df["rank_pat_negative"]    = df["pat_negative"] * 100  # binary: 0 or 100

    # Health factors (higher raw = lower stress)
    df["rank_pat_margin"]      = df.groupby("sector")["pat_margin"].transform(percentile_rank_health)
    df["rank_rev_growth"]      = df.groupby("sector")["rev_growth_qoq"].transform(percentile_rank_health)
    df["rank_cash_ratio"]      = df.groupby("sector")["cash_ratio"].transform(percentile_rank_health)

    # Weighted average stress score
    weights = {
        "rank_debt_equity":     0.25,
        "rank_net_debt_ebitda": 0.25,
        "rank_pat_margin":      0.20,
        "rank_rev_growth":      0.15,
        "rank_cash_ratio":      0.10,
        "rank_pat_negative":    0.05,
    }

    df["stress_raw"] = sum(
        df[col].fillna(50) * w  # fill NA with neutral 50
        for col, w in weights.items()
    )

    # Normalize within sector 0-100
    df["financial_stress_score"] = df.groupby("sector")["stress_raw"].transform(
        lambda x: ((x - x.min()) / (x.max() - x.min() + 1e-10)) * 100
    ).round(2)

    df["score_date"]    = score_date
    df["score_version"] = "v1.0"

    keep = [
        "isin", "score_date", "sector", "cap_cat",
        "financial_stress_score",
        "debt_equity", "net_debt_ebitda", "pat_margin",
        "rev_growth_qoq", "cash_ratio", "pat_negative",
        "revenue_cr", "ebitda_cr", "pat_cr", "total_debt_cr", "cash_cr", "equity_cr",
        "period_end", "score_version",
    ]

    result = df[[c for c in keep if c in df.columns]].copy()
    result = result.sort_values("financial_stress_score", ascending=False).reset_index(drop=True)

    logger.info("Stress scores — min=%.1f max=%.1f mean=%.1f",
                result["financial_stress_score"].min(),
                result["financial_stress_score"].max(),
                result["financial_stress_score"].mean())

    return result


def write_score_to_r2(df: pd.DataFrame, score_date: date) -> str:
    if df.empty: return ""
    key = (f"{ENV}/scores/financial_stress"
           f"/year={score_date.year}/month={score_date.month:02d}"
           f"/financial_stress_{score_date.strftime('%Y%m%d')}.parquet")
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


def run_for_date(score_date: date) -> dict:
    fin_df  = load_financials(score_date)
    if fin_df.empty:
        return {"date": str(score_date), "status": "no_data"}

    ratios  = compute_ratios(fin_df)
    scores  = compute_stress_score(ratios, score_date)

    # Print most and least stressed
    logger.info("\nMost stressed (top 10):\n%s",
                scores.head(10)[["isin","sector","financial_stress_score","debt_equity","pat_margin"]].to_string())
    logger.info("\nLeast stressed (bottom 10):\n%s",
                scores.tail(10)[["isin","sector","financial_stress_score","debt_equity","pat_margin"]].to_string())

    path = write_score_to_r2(scores, score_date)
    return {"date": str(score_date), "isins": len(scores), "path": path, "status": "ok"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=date.fromisoformat)
    args = parser.parse_args()
    target = args.date or datetime.now(IST).date()
    print(run_for_date(target))


if __name__ == "__main__":
    main()
