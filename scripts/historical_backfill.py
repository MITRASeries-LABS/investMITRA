"""
investMITRA — Historical Backfill
Fetches NSE + BSE equity prices for a date range and loads to Neon.

Runs via GitHub Actions (historical_backfill.yml) or locally:
  python scripts/historical_backfill.py --start 2014-01-01 --end 2026-08-02

GitHub Actions 6-hour limit = ~180 trading days per run.
For full 10-year backfill, run multiple times with different date ranges:
  2014-2017, 2018-2020, 2021-2023, 2024-2026
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import psycopg2
import requests

from dotenv import load_dotenv
load_dotenv('.env.prod')

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

# NSE full file URL (has delivery %)
NSE_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{date}.csv"
# BSE URL
BSE_URL = "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{date}_F_0000.CSV"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer":    "https://www.nseindia.com",
}

# NSE column mapping
NSE_COL_MAP = {
    "SYMBOL":        "nse_symbol",
    "SERIES":        "series",
    "OPEN_PRICE":    "open",
    "HIGH_PRICE":    "high",
    "LOW_PRICE":     "low",
    "CLOSE_PRICE":   "close",
    "AVG_PRICE":     "vwap",
    "TTL_TRD_QNTY":  "volume",
    "TURNOVER_LACS": "turnover_lacs",
    "NO_OF_TRADES":  "total_trades",
    "DELIV_QTY":     "delivery_qty",
    "DELIV_PER":     "delivery_pct",
}

# BSE column mapping
BSE_COL_MAP = {
    "ISIN":            "isin",
    "FinInstrmId":     "bse_code",
    "TckrSymb":        "nse_symbol",
    "SctySrs":         "group",
    "OpnPric":         "open",
    "HghPric":         "high",
    "LwPric":          "low",
    "ClsPric":         "close",
    "TtlTradgVol":     "volume",
    "TtlTrfVal":       "turnover_rs",
    "TtlNbOfTxsExctd": "total_trades",
}

EQUITY_SERIES = {"EQ", "BE", "BZ", "BL", "ST", "SM"}

_SYMBOL_TO_ISIN: dict[str, str] = {}
_session = None


def get_session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(HEADERS)
        _session.get("https://www.nseindia.com", timeout=15)
        time.sleep(2)
    return _session


def get_symbol_to_isin() -> dict[str, str]:
    global _SYMBOL_TO_ISIN
    if _SYMBOL_TO_ISIN:
        return _SYMBOL_TO_ISIN
    neon_url = os.getenv("CC_POSTGRES_URL")
    conn = psycopg2.connect(neon_url, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute(
        "SELECT nse_symbol, isin FROM investmitra.company_master "
        "WHERE nse_symbol IS NOT NULL AND isin IS NOT NULL"
    )
    _SYMBOL_TO_ISIN = {r[0]: r[1] for r in cur.fetchall()}
    cur.close(); conn.close()
    logger.info("Loaded %d symbol->ISIN mappings", len(_SYMBOL_TO_ISIN))
    return _SYMBOL_TO_ISIN


def fetch_nse(target_date: date) -> pd.DataFrame | None:
    url = NSE_URL.format(date=target_date.strftime("%d%m%Y"))
    try:
        resp = get_session().get(url, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        import io
        df = pd.read_csv(io.StringIO(resp.text), dtype=str)
        df.columns = df.columns.str.strip()
        df = df.rename(columns={k: v for k, v in NSE_COL_MAP.items() if k in df.columns})

        if "series" in df.columns:
            df = df[df["series"].str.strip().isin(EQUITY_SERIES)].copy()

        for col in ["open", "high", "low", "close", "vwap"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("Int64")

        if "delivery_pct" in df.columns:
            df["delivery_pct"] = pd.to_numeric(df["delivery_pct"], errors="coerce")

        if "turnover_lacs" in df.columns:
            df["turnover_cr"] = pd.to_numeric(df["turnover_lacs"], errors="coerce") / 100.0

        # ISIN enrichment
        sym_map = get_symbol_to_isin()
        df["isin"] = df["nse_symbol"].map(sym_map)
        df["trade_date"] = target_date
        df["source"]     = "NSE"

        df = df[df["isin"].notna() & (df["isin"].astype(str).str.len() == 12)].copy()
        return df if not df.empty else None

    except Exception as e:
        logger.warning("NSE fetch failed %s: %s", target_date, e)
        return None


def fetch_bse(target_date: date) -> pd.DataFrame | None:
    url = BSE_URL.format(date=target_date.strftime("%Y%m%d"))
    try:
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.bseindia.com"
        }, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        import io
        df = pd.read_csv(io.StringIO(resp.text), dtype=str, on_bad_lines="skip")
        df.columns = df.columns.str.strip()
        df = df.rename(columns={k: v for k, v in BSE_COL_MAP.items() if k in df.columns})

        if "group" in df.columns:
            df = df[df["group"].isin({"A", "B", "E", "F", "S", "T", "XT", "Z", "X"})].copy()

        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("Int64")

        if "turnover_rs" in df.columns:
            df["turnover_cr"] = pd.to_numeric(df["turnover_rs"], errors="coerce") / 10000000.0

        df["trade_date"] = target_date
        df["source"]     = "BSE"

        if "isin" in df.columns:
            df = df[df["isin"].notna() & (df["isin"].astype(str).str.len() == 12)].copy()

        return df if not df.empty else None

    except Exception as e:
        logger.warning("BSE fetch failed %s: %s", target_date, e)
        return None


def write_to_neon(df: pd.DataFrame, source: str) -> int:
    from psycopg2.extras import execute_values

    def sf(val):
        try:
            f = float(val)
            return None if f != f else f
        except: return None

    def si(val):
        try: return int(val) if pd.notna(val) else None
        except: return None

    rows = []
    for _, row in df.iterrows():
        isin = str(row.get("isin", "")).strip()
        if not isin or len(isin) != 12:
            continue
        rows.append((
            isin,
            row["trade_date"],
            source,
            sf(row.get("open")), sf(row.get("high")),
            sf(row.get("low")), sf(row.get("close")),
            sf(row.get("vwap")),
            si(row.get("volume")),
            sf(row.get("turnover_cr")),
            sf(row.get("delivery_pct")),
            85,
        ))

    if not rows:
        return 0

    neon_url = os.getenv("CC_POSTGRES_URL")
    conn = psycopg2.connect(neon_url, connect_timeout=15)
    conn.autocommit = False
    cur  = conn.cursor()

    execute_values(
        cur,
        """
        INSERT INTO investmitra.equity_prices
            (isin, trade_date, source,
             open, high, low, close, vwap,
             volume, turnover_cr, delivery_pct,
             quality_score, ingested_at)
        VALUES %s
        ON CONFLICT (isin, trade_date, source) DO NOTHING
        """,
        rows,
        template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())",
        page_size=1000
    )

    conn.commit()
    cur.close(); conn.close()
    return len(rows)


def is_trading_day(d: date) -> bool:
    """Skip weekends. NSE/BSE holidays handled by 404 response."""
    return d.weekday() < 5


def run_backfill(start: date, end: date):
    logger.info("Starting backfill: %s to %s", start, end)
    total_days  = 0
    total_nse   = 0
    total_bse   = 0
    skipped     = 0

    current = start
    while current <= end:
        if not is_trading_day(current):
            current += timedelta(days=1)
            continue

        logger.info("Processing %s...", current)

        # Fetch NSE
        nse_df = fetch_nse(current)
        if nse_df is not None:
            written = write_to_neon(nse_df, "NSE")
            total_nse += written
            logger.info("  NSE: %d rows", written)
        else:
            logger.info("  NSE: no data (holiday or unavailable)")
            skipped += 1

        time.sleep(0.5)

        # Fetch BSE
        bse_df = fetch_bse(current)
        if bse_df is not None:
            written = write_to_neon(bse_df, "BSE")
            total_bse += written
            logger.info("  BSE: %d rows", written)
        else:
            logger.info("  BSE: no data")

        time.sleep(1.0)  # polite delay
        total_days += 1
        current    += timedelta(days=1)

        # Progress summary every 50 days
        if total_days % 50 == 0:
            logger.info("Progress: %d days processed — NSE total: %d, BSE total: %d",
                        total_days, total_nse, total_bse)

    logger.info("Backfill complete: %d trading days, NSE: %d rows, BSE: %d rows, skipped: %d",
                total_days, total_nse, total_bse, skipped)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat,
                        default=date.fromisoformat(os.getenv("START_DATE", "2024-01-01")))
    parser.add_argument("--end",   type=date.fromisoformat,
                        default=date.fromisoformat(os.getenv("END_DATE", "2026-08-02")))
    args = parser.parse_args()

    run_backfill(args.start, args.end)


if __name__ == "__main__":
    main()
