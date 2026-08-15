"""
investMITRA — Management Quality Score (Score 3)
Computes a 0-100 management quality score for each ISIN.
Higher score = BETTER management quality.

Inputs (via yfinance major_holders + institutional_holders):
  - Promoter/Insider holding % (higher = better alignment with shareholders)
  - Institution count (higher = better governance coverage)
  - Institutional holding % (higher = more institutional confidence)

Methodology:
  1. Fetch holder data for all NSE stocks via yfinance
  2. Load into ownership_data table
  3. Rank within sector
  4. Weighted average = management quality score 0-100

Output: Parquet on R2
  cc-raw/prod/scores/management_quality/year={Y}/month={M}/management_quality_{YYYYMMDD}.parquet
"""
from __future__ import annotations
import argparse, io, logging, os, time
from datetime import date, datetime, timedelta, timezone
import boto3, pandas as pd, numpy as np
import psycopg2
from psycopg2.extras import execute_values
import pyarrow as pa
import pyarrow.parquet as pq
import yfinance as yf
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
    cur.execute("SELECT isin, nse_symbol, COALESCE(sector,'Unknown'), market_cap_category FROM investmitra.company_master WHERE isin IS NOT NULL AND nse_symbol IS NOT NULL")
    _SECTOR_MAP = {r[0]: {"symbol": r[1], "sector": r[2], "cap_cat": r[3]} for r in cur.fetchall()}
    cur.close(); conn.close()
    logger.info("Loaded %d sector mappings", len(_SECTOR_MAP))
    return _SECTOR_MAP


def fetch_holder_data(isin: str, symbol: str) -> dict | None:
    """Fetch holder data for one stock via yfinance."""
    try:
        t = yf.Ticker(f"{symbol}.NS")
        mh = t.major_holders
        if mh is None or mh.empty:
            return None

        # major_holders has Value column indexed by Breakdown
        mh_dict = mh.set_index("Breakdown")["Value"].to_dict() if "Breakdown" in mh.columns else {}
        if not mh_dict and len(mh) > 0:
            # Try alternate format
            mh_dict = mh.iloc[:, 0].to_dict()

        insider_pct       = float(mh_dict.get("insidersPercentHeld", np.nan))
        institution_pct   = float(mh_dict.get("institutionsPercentHeld", np.nan))
        institution_count = float(mh_dict.get("institutionsCount", np.nan))

        if all(np.isnan(v) for v in [insider_pct, institution_pct, institution_count]):
            return None

        return {
            "isin":             isin,
            "insider_pct":      insider_pct * 100 if not np.isnan(insider_pct) else None,
            "institution_pct":  institution_pct * 100 if not np.isnan(institution_pct) else None,
            "institution_count": institution_count if not np.isnan(institution_count) else None,
        }
    except Exception as e:
        logger.debug("Failed %s: %s", symbol, e)
        return None


def fetch_all_holders(symbols: list[tuple]) -> pd.DataFrame:
    """Fetch holder data for all stocks."""
    records = []
    failed  = 0

    logger.info("Fetching holder data for %d stocks...", len(symbols))

    for i, (isin, info) in enumerate(symbols.items()):
        symbol = info["symbol"]
        data   = fetch_holder_data(isin, symbol)
        if data:
            records.append(data)
        else:
            failed += 1

        if i % 100 == 0 and i > 0:
            logger.info("Progress: %d/%d — records: %d failed: %d",
                        i, len(symbols), len(records), failed)
        time.sleep(0.3)

    logger.info("Done: %d records, %d failed", len(records), failed)
    df = pd.DataFrame(records)
    return df


def write_holders_to_neon(df: pd.DataFrame, as_of_date: date) -> int:
    """Write holder data to ownership_data table."""
    if df.empty: return 0

    # Check if source exists
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = True
    cur  = conn.cursor()
    cur.execute("INSERT INTO investmitra.source_registry (source_id, domain, description, refresh_frequency, is_active) VALUES ('yfinance_holders', 'ownership', 'Yahoo Finance major holders data', 'quarterly', TRUE) ON CONFLICT (source_id) DO NOTHING")
    cur.close(); conn.close()

    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = False
    cur  = conn.cursor()

    rows = [
        (r["isin"], as_of_date, as_of_date,
         r.get("insider_pct"),    # promoter_pct proxy
         None,                    # promoter_pledged_pct
         r.get("institution_pct"), # fii_pct proxy
         r.get("institution_pct"), # dii_pct (same as institution for now)
         None,                    # mf_pct
         None,                    # public_pct
         int(r.get("institution_count") or 0),
         80, "yfinance_holders", None)
        for _, r in df.iterrows()
        if r.get("isin")
    ]

    execute_values(cur, """
        INSERT INTO investmitra.ownership_data
            (isin, period_end, filing_date,
             promoter_pct, promoter_pledged_pct,
             fii_pct, dii_pct, mf_pct, public_pct,
             total_shareholders,
             quality_score, source_id, source_doc_url)
        VALUES %s
        ON CONFLICT DO NOTHING
    """, rows, page_size=200)

    conn.commit(); cur.close(); conn.close()
    logger.info("Written %d ownership records to Neon", len(rows))
    return len(rows)


def compute_management_score(df: pd.DataFrame, score_date: date) -> pd.DataFrame:
    """Compute management quality score 0-100 (higher = better quality)."""
    sm = get_sector_map()
    df = df.copy()
    df["sector"]  = df["isin"].map(lambda x: sm.get(x, {}).get("sector", "Unknown"))
    df["cap_cat"] = df["isin"].map(lambda x: sm.get(x, {}).get("cap_cat", "MICRO"))

    # Cap values
    df["insider_pct"]       = df["insider_pct"].clip(0, 90)
    df["institution_pct"]   = df["institution_pct"].clip(0, 80)
    df["institution_count"] = df["institution_count"].clip(0, 2000)

    def prank_high(s):
        return s.rank(pct=True, na_option='keep') * 100

    # Higher insider holding = better (promoter skin in game)
    df["r_insider"]      = df.groupby("sector")["insider_pct"].transform(prank_high)
    # Higher institutional holding = better (smart money confidence)
    df["r_institution"]  = df.groupby("sector")["institution_pct"].transform(prank_high)
    # More institutions = better coverage/governance
    df["r_inst_count"]   = df.groupby("sector")["institution_count"].transform(prank_high)

    weights = {
        "r_insider":     0.50,
        "r_institution": 0.30,
        "r_inst_count":  0.20,
    }

    df["quality_raw"] = sum(df[c].fillna(50) * w for c, w in weights.items())

    df["management_quality_score"] = df.groupby("sector")["quality_raw"].transform(
        lambda x: ((x - x.min()) / (x.max() - x.min() + 1e-10)) * 100
    ).round(2)

    df["score_date"]    = score_date
    df["score_version"] = "v1.0"

    keep = ["isin","score_date","sector","cap_cat","management_quality_score",
            "insider_pct","institution_pct","institution_count","score_version"]

    result = df[[c for c in keep if c in df.columns]].sort_values(
        "management_quality_score", ascending=False).reset_index(drop=True)

    logger.info("Scores: min=%.1f max=%.1f mean=%.1f",
                result["management_quality_score"].min(),
                result["management_quality_score"].max(),
                result["management_quality_score"].mean())

    logger.info("Top 10 management quality:\n%s",
                result.head(10)[["isin","sector","management_quality_score",
                                  "insider_pct","institution_pct"]].to_string())
    logger.info("Bottom 10:\n%s",
                result.tail(10)[["isin","sector","management_quality_score",
                                   "insider_pct","institution_pct"]].to_string())
    return result


def write_to_r2(df: pd.DataFrame, score_date: date) -> str:
    if df.empty: return ""
    key = (f"{ENV}/scores/management_quality/year={score_date.year}"
           f"/month={score_date.month:02d}/management_quality_{score_date.strftime('%Y%m%d')}.parquet")
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
    parser.add_argument("--date",       type=date.fromisoformat)
    parser.add_argument("--fetch-only", action="store_true", help="Only fetch holders, don't score")
    parser.add_argument("--score-only", action="store_true", help="Only score from existing DB data")
    args   = parser.parse_args()
    target = args.date or datetime.now(IST).date()

    sm = get_sector_map()

    if not args.score_only:
        # Fetch holder data
        df_holders = fetch_all_holders(sm)
        if not df_holders.empty:
            write_holders_to_neon(df_holders, target)
    else:
        # Load from DB
        conn = psycopg2.connect(NEON_URL, connect_timeout=15)
        cur  = conn.cursor()
        cur.execute("""
            SELECT isin, promoter_pct, fii_pct, total_shareholders
            FROM investmitra.ownership_data
            WHERE source_id = 'yfinance_holders'
            ORDER BY filing_date DESC
        """)
        rows = cur.fetchall()
        cur.close(); conn.close()
        df_holders = pd.DataFrame(rows, columns=["isin","insider_pct","institution_pct","institution_count"])

    if df_holders.empty:
        logger.warning("No holder data available")
        return

    # Compute scores
    scores = compute_management_score(df_holders, target)
    path   = write_to_r2(scores, target)
    print({"date": str(target), "isins": len(scores), "path": path, "status": "ok"})


if __name__ == "__main__":
    main()
