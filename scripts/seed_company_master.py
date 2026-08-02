"""
investMITRA — Company Master Seeding
Loads all active NSE and BSE listed stocks into company_master table.

Sources:
  NSE: https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv
  BSE: https://www.bseindia.com/corporates/List_Scrips.html (download)

Run once to seed, then run weekly to pick up new listings/delistings.
Usage:
  python scripts/seed_company_master.py
  python scripts/seed_company_master.py --update   # only add new ISINs
"""

from __future__ import annotations

import argparse
import io
import logging
import time
from datetime import date

import pandas as pd
import psycopg2
import requests
from dotenv import load_dotenv
import os

load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NEON_URL = os.getenv("CC_POSTGRES_URL")

# NSE equity list URL
NSE_EQUITY_LIST = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

# BSE equity list URL
BSE_EQUITY_LIST = "https://www.bseindia.com/corporates/List_Scrips.html"
BSE_DOWNLOAD    = "https://www.bseindia.com/corporates/downloadfile.aspx?FileName=Listofscripts.xlsx&FilePath=Scripmaster&FileExt=xlsx&strName=Listofscripts"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":    "https://www.nseindia.com",
}

# Market cap category thresholds (approximate, based on index membership)
# Will be updated properly when we have price data
LARGE_CAP_SYMBOLS = set()  # Nifty 100
MID_CAP_SYMBOLS   = set()  # Nifty Midcap 150
SMALL_CAP_SYMBOLS = set()  # rest


def fetch_nse_list() -> pd.DataFrame:
    """Fetch NSE equity list — returns ISIN, symbol, company name, series."""
    logger.info("Fetching NSE equity list...")
    session = requests.Session()
    session.headers.update(HEADERS)

    # NSE requires a session cookie
    session.get("https://www.nseindia.com", timeout=15)
    time.sleep(1)

    resp = session.get(NSE_EQUITY_LIST, timeout=30)
    resp.raise_for_status()

    df = pd.read_csv(io.StringIO(resp.text), dtype=str)
    df.columns = df.columns.str.strip()

    logger.info("NSE list: %d rows, columns: %s", len(df), df.columns.tolist())

    # Standard NSE column names
    col_map = {}
    for col in df.columns:
        col_lower = col.lower().strip()
        if "isin" in col_lower:
            col_map[col] = "isin"
        elif "symbol" in col_lower:
            col_map[col] = "nse_symbol"
        elif "name" in col_lower or "company" in col_lower:
            col_map[col] = "company_name"
        elif "series" in col_lower:
            col_map[col] = "series"
        elif "date" in col_lower and "list" in col_lower:
            col_map[col] = "listing_date"
        elif "sector" in col_lower or "industry" in col_lower:
            col_map[col] = "sector"

    df = df.rename(columns=col_map)

    # Filter EQ series only
    if "series" in df.columns:
        df = df[df["series"].str.strip() == "EQ"].copy()

    logger.info("NSE list after EQ filter: %d rows", len(df))
    return df


def fetch_bse_list() -> pd.DataFrame:
    """Fetch BSE equity list — returns ISIN, BSE code, company name."""
    logger.info("Fetching BSE equity list...")
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.bseindia.com",
    })

    try:
        resp = session.get(BSE_DOWNLOAD, timeout=30)
        resp.raise_for_status()
        df = pd.read_excel(io.BytesIO(resp.content), dtype=str)
    except Exception as e:
        logger.warning("BSE Excel download failed: %s — trying HTML", e)
        try:
            # Fallback: parse HTML table
            resp = session.get(BSE_EQUITY_LIST, timeout=30)
            tables = pd.read_html(resp.text)
            df = tables[0] if tables else pd.DataFrame()
        except Exception as e2:
            logger.error("BSE list fetch failed: %s", e2)
            return pd.DataFrame()

    df.columns = df.columns.str.strip()
    logger.info("BSE list: %d rows, columns: %s", len(df), df.columns.tolist())

    col_map = {}
    for col in df.columns:
        col_lower = col.lower().strip()
        if "isin" in col_lower:
            col_map[col] = "isin"
        elif "scrip" in col_lower and "code" in col_lower:
            col_map[col] = "bse_code"
        elif "scrip" in col_lower and "name" in col_lower:
            col_map[col] = "company_name_bse"
        elif "status" in col_lower:
            col_map[col] = "status"
        elif "sector" in col_lower:
            col_map[col] = "sector"
        elif "industry" in col_lower:
            col_map[col] = "industry"

    df = df.rename(columns=col_map)

    # Filter active only
    if "status" in df.columns:
        df = df[df["status"].str.upper().str.strip() == "A"].copy()

    if "bse_code" in df.columns:
        df["bse_code"] = df["bse_code"].str.zfill(6)

    logger.info("BSE list after active filter: %d rows", len(df))
    return df


def merge_lists(nse_df: pd.DataFrame, bse_df: pd.DataFrame) -> pd.DataFrame:
    """Merge NSE and BSE lists on ISIN."""
    logger.info("Merging NSE and BSE lists on ISIN...")

    merged = nse_df.copy()

    if "isin" in bse_df.columns and "isin" in merged.columns:
        bse_cols = ["isin"]
        if "bse_code"        in bse_df.columns: bse_cols.append("bse_code")
        if "sector"          in bse_df.columns: bse_cols.append("sector")
        if "industry"        in bse_df.columns: bse_cols.append("industry")

        merged = merged.merge(
            bse_df[bse_cols].drop_duplicates("isin"),
            on="isin", how="left"
        )

    # Fill missing sector from BSE if not in NSE
    if "sector_x" in merged.columns:
        merged["sector"] = merged["sector_x"].fillna(merged.get("sector_y", ""))
        merged = merged.drop(columns=["sector_x", "sector_y"], errors="ignore")

    logger.info("Merged: %d unique ISINs", merged["isin"].nunique())
    return merged


def seed_to_neon(df: pd.DataFrame, update_only: bool = False) -> dict:
    """Insert/update company_master in Neon."""
    logger.info("Connecting to Neon...")
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = False
    cur = conn.cursor()

    inserted = 0
    updated  = 0
    skipped  = 0

    logger.info("Seeding %d companies to company_master...", len(df))

    for _, row in df.iterrows():
        isin = str(row.get("isin", "")).strip()
        if not isin or len(isin) != 12:
            skipped += 1
            continue

        nse_symbol   = str(row.get("nse_symbol", "") or "").strip() or None
        bse_code     = str(row.get("bse_code", "") or "").strip() or None
        company_name = str(row.get("company_name", "") or "").strip()
        sector       = str(row.get("sector", "") or "").strip() or None
        industry     = str(row.get("industry", "") or "").strip() or None

        listing_date = None
        if "listing_date" in row and pd.notna(row["listing_date"]):
            try:
                listing_date = pd.to_datetime(row["listing_date"], dayfirst=True).date()
            except Exception:
                pass

        try:
            cur.execute(
                """
                INSERT INTO investmitra.company_master
                    (isin, nse_symbol, bse_code, company_name,
                     sector, industry, listing_date, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
                ON CONFLICT (isin) DO UPDATE SET
                    nse_symbol   = COALESCE(EXCLUDED.nse_symbol, company_master.nse_symbol),
                    bse_code     = COALESCE(EXCLUDED.bse_code,   company_master.bse_code),
                    company_name = EXCLUDED.company_name,
                    sector       = COALESCE(EXCLUDED.sector,     company_master.sector),
                    industry     = COALESCE(EXCLUDED.industry,   company_master.industry),
                    is_active    = TRUE,
                    updated_at   = NOW()
                """,
                (isin, nse_symbol, bse_code, company_name,
                 sector, industry, listing_date)
            )
            if cur.rowcount == 1:
                inserted += 1
            else:
                updated += 1
        except Exception as e:
            logger.warning("Failed %s %s: %s", isin, company_name, e)
            skipped += 1

    conn.commit()
    cur.close()
    conn.close()

    result = {"inserted": inserted, "updated": updated, "skipped": skipped}
    logger.info("Seeding complete: %s", result)
    return result


def verify(n: int = 10):
    """Quick verification query."""
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM investmitra.company_master WHERE is_active = TRUE")
    total = cur.fetchone()[0]

    cur.execute(
        """
        SELECT isin, nse_symbol, bse_code, company_name, sector
        FROM investmitra.company_master
        WHERE is_active = TRUE
        ORDER BY isin
        LIMIT %s
        """,
        (n,)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    print(f"\nTotal active companies: {total}")
    print(f"\nSample ({n} rows):")
    print(f"{'ISIN':<14} {'NSE':<12} {'BSE':<8} {'Name':<40} {'Sector'}")
    print("-" * 100)
    for row in rows:
        print(f"{row[0]:<14} {str(row[1] or ''):<12} {str(row[2] or ''):<8} {str(row[3] or '')[:40]:<40} {row[4] or ''}")


def main():
    parser = argparse.ArgumentParser(description="Seed company_master table")
    parser.add_argument("--update", action="store_true", help="Update existing records too")
    parser.add_argument("--verify", action="store_true", help="Just verify current state")
    args = parser.parse_args()

    if args.verify:
        verify()
        return

    # Fetch both lists
    nse_df = fetch_nse_list()
    bse_df = fetch_bse_list()

    if nse_df.empty:
        logger.error("NSE list is empty — aborting")
        return

    # Merge
    merged = merge_lists(nse_df, bse_df)

    # Seed
    result = seed_to_neon(merged, update_only=args.update)

    # Verify
    verify(20)


if __name__ == "__main__":
    main()
