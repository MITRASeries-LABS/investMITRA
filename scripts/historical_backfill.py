"""
investMITRA — Historical Backfill v3
NSE old archive format added for pre-2019 dates.

URL formats:
  NSE new (2019+): https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{DDMMYYYY}.csv
  NSE old (pre-2019): https://nsearchives.nseindia.com/content/historical/EQUITIES/{YYYY}/{MMM}/cm{DD}{MMM}{YYYY}bhav.csv.zip
  BSE: https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{YYYYMMDD}_F_0000.CSV
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import time
import zipfile
from datetime import date, datetime, timedelta, timezone

import boto3
import pandas as pd
import psycopg2
import pyarrow as pa
import pyarrow.parquet as pq
import requests
from dotenv import load_dotenv
from psycopg2.extras import execute_values

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
NEON_WINDOW_DAYS = 90

# NSE URLs
NSE_NEW_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{date}.csv"
NSE_OLD_URL = "https://nsearchives.nseindia.com/content/historical/EQUITIES/{year}/{month}/cm{dd}{month}{year}bhav.csv.zip"
BSE_URL     = "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{date}_F_0000.CSV"

# Cutoff for new vs old NSE format
NSE_NEW_FORMAT_DATE = date(2024, 7, 8)  # NSE circular 62424 dated June 12, 2024

HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"}

# Old NSE column mapping
NSE_OLD_COL_MAP = {
    "SYMBOL":      "nse_symbol",
    "SERIES":      "series",
    "OPEN":        "open",
    "HIGH":        "high",
    "LOW":         "low",
    "CLOSE":       "close",
    "LAST":        "last_price",
    "PREVCLOSE":   "prev_close",
    "TOTTRDQTY":   "volume",
    "TOTTRDVAL":   "turnover_rs",
    "TOTALTRADES": "total_trades",
    "ISIN":        "isin",
}

# New NSE column mapping
NSE_NEW_COL_MAP = {
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

BSE_COL_MAP = {
    "ISIN": "isin", "FinInstrmId": "bse_code", "TckrSymb": "nse_symbol",
    "SctySrs": "group", "OpnPric": "open", "HghPric": "high",
    "LwPric": "low", "ClsPric": "close", "TtlTradgVol": "volume",
    "TtlTrfVal": "turnover_rs", "TtlNbOfTxsExctd": "total_trades",
}

EQUITY_SERIES = {"EQ", "BE", "BZ", "BL", "ST", "SM"}
_SYMBOL_TO_ISIN: dict[str, str] = {}
_session = None
_s3_client = None


def get_session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(HEADERS)
        _session.get("https://www.nseindia.com", timeout=15)
        time.sleep(2)
    return _session


def get_s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3",
            endpoint_url=AWS_ENDPOINT,
            aws_access_key_id=AWS_KEY,
            aws_secret_access_key=AWS_SECRET,
            region_name="auto")
    return _s3_client


def get_symbol_to_isin() -> dict[str, str]:
    global _SYMBOL_TO_ISIN
    if _SYMBOL_TO_ISIN:
        return _SYMBOL_TO_ISIN
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("SELECT nse_symbol, isin FROM investmitra.company_master WHERE nse_symbol IS NOT NULL AND isin IS NOT NULL")
    _SYMBOL_TO_ISIN = {r[0]: r[1] for r in cur.fetchall()}
    cur.close(); conn.close()
    logger.info("Loaded %d symbol->ISIN mappings", len(_SYMBOL_TO_ISIN))
    return _SYMBOL_TO_ISIN


def file_exists_in_r2(source_id: str, target_date: date) -> bool:
    prefix = (f"{ENV}/market_data/equity_prices"
              f"/year={target_date.year}/month={target_date.month:02d}"
              f"/day={target_date.day:02d}/{source_id}_")
    result = get_s3().list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    return len(result.get("Contents", [])) > 0


def write_to_r2(df: pd.DataFrame, source_id: str, target_date: date) -> int:
    if df.empty: return 0
    key = (f"{ENV}/market_data/equity_prices"
           f"/year={target_date.year}/month={target_date.month:02d}"
           f"/day={target_date.day:02d}"
           f"/{source_id}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.parquet")
    table = pa.Table.from_pandas(df, preserve_index=False)
    buf   = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    buf.seek(0)
    get_s3().put_object(Bucket=BUCKET, Key=key, Body=buf.read())
    return len(df)


def write_to_neon(df: pd.DataFrame, source: str) -> int:
    if df.empty: return 0

    def sf(v):
        try: f=float(v); return None if f!=f else f
        except: return None
    def si(v):
        try: return int(v) if pd.notna(v) else None
        except: return None

    rows = [(str(r.get("isin","")).strip(), r["trade_date"], source,
             sf(r.get("open")), sf(r.get("high")), sf(r.get("low")), sf(r.get("close")),
             sf(r.get("vwap")), si(r.get("volume")), sf(r.get("turnover_cr")),
             sf(r.get("delivery_pct")), 85)
            for _, r in df.iterrows()
            if str(r.get("isin","")).strip() and len(str(r.get("isin","")).strip()) == 12]

    if not rows: return 0

    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = False
    cur  = conn.cursor()
    execute_values(cur,
        """INSERT INTO investmitra.equity_prices
           (isin,trade_date,source,open,high,low,close,vwap,
            volume,turnover_cr,delivery_pct,quality_score,ingested_at)
           VALUES %s ON CONFLICT (isin,trade_date,source) DO NOTHING""",
        rows, template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())", page_size=1000)
    conn.commit(); cur.close(); conn.close()
    return len(rows)


def fetch_nse_old(target_date: date) -> pd.DataFrame | None:
    """Fetch NSE Bhavcopy using old archive format (pre-July 2024)."""
    url = NSE_OLD_URL.format(
        year=target_date.strftime("%Y"),
        month=target_date.strftime("%b").upper(),
        dd=target_date.strftime("%d"),
    )
    try:
        resp = get_session().get(url, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            df = pd.read_csv(z.open(z.namelist()[0]), dtype=str)

        df.columns = df.columns.str.strip()
        df = df.rename(columns={k: v for k, v in NSE_OLD_COL_MAP.items() if k in df.columns})

        if "series" in df.columns:
            df = df[df["series"].str.strip().isin(EQUITY_SERIES)].copy()

        for col in ["open", "high", "low", "close", "last_price", "prev_close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("Int64")

        if "turnover_rs" in df.columns:
            df["turnover_cr"] = pd.to_numeric(df["turnover_rs"], errors="coerce") / 100000.0

        df["trade_date"]   = target_date
        df["source"]       = "NSE"
        df["vwap"]         = None
        df["delivery_pct"] = None

        if "isin" in df.columns:
            df = df[df["isin"].notna() & (df["isin"].astype(str).str.len() == 12)].copy()

        return df if not df.empty else None

    except Exception as e:
        logger.warning("NSE old fetch failed %s: %s", target_date, e)
        return None


def fetch_nse_new(target_date: date) -> pd.DataFrame | None:
    """Fetch NSE full file with delivery % (post-July 2024)."""
    url = NSE_NEW_URL.format(date=target_date.strftime("%d%m%Y"))
    try:
        resp = get_session().get(url, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        df = pd.read_csv(io.StringIO(resp.text), dtype=str)
        df.columns = df.columns.str.strip()
        df = df.rename(columns={k: v for k, v in NSE_NEW_COL_MAP.items() if k in df.columns})

        if "series" in df.columns:
            df = df[df["series"].str.strip().isin(EQUITY_SERIES)].copy()

        for col in ["open","high","low","close","vwap","delivery_pct"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("Int64")

        if "turnover_lacs" in df.columns:
            df["turnover_cr"] = pd.to_numeric(df["turnover_lacs"], errors="coerce") / 100.0

        sym_map = get_symbol_to_isin()
        df["isin"]       = df["nse_symbol"].map(sym_map)
        df["trade_date"] = target_date
        df["source"]     = "NSE"

        df = df[df["isin"].notna() & (df["isin"].astype(str).str.len() == 12)].copy()
        return df if not df.empty else None

    except Exception as e:
        logger.warning("NSE new fetch failed %s: %s", target_date, e)
        return None


def fetch_nse(target_date: date) -> pd.DataFrame | None:
    if target_date >= NSE_NEW_FORMAT_DATE:
        return fetch_nse_new(target_date)
    else:
        return fetch_nse_old(target_date)


def fetch_bse(target_date: date) -> pd.DataFrame | None:
    url = BSE_URL.format(date=target_date.strftime("%Y%m%d"))
    try:
        resp = requests.get(url, headers={"User-Agent":"Mozilla/5.0","Referer":"https://www.bseindia.com"}, timeout=30)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text), dtype=str, on_bad_lines="skip")
        df.columns = df.columns.str.strip()
        df = df.rename(columns={k: v for k, v in BSE_COL_MAP.items() if k in df.columns})
        if "group" in df.columns:
            df = df[df["group"].isin({"A","B","E","F","S","T","XT","Z","X"})].copy()
        for col in ["open","high","low","close"]:
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
        logger.warning("BSE failed %s: %s", target_date, e)
        return None


def run_backfill(start: date, end: date):
    today       = datetime.now(IST).date()
    neon_cutoff = today - timedelta(days=NEON_WINDOW_DAYS)
    total_days  = 0
    total_nse   = 0
    total_bse   = 0

    logger.info("Backfill: %s to %s | Neon: last %d days", start, end, NEON_WINDOW_DAYS)

    current = start
    while current <= end:
        if current.weekday() >= 5:
            current += timedelta(days=1)
            continue

        load_to_neon = current >= neon_cutoff
        nse_exists   = file_exists_in_r2("nse_bhavcopy", current)
        bse_exists   = file_exists_in_r2("bse_eod", current)

        if nse_exists and bse_exists and not load_to_neon:
            current += timedelta(days=1)
            total_days += 1
            continue

        logger.info("Processing %s (neon=%s)...", current, load_to_neon)

        if not nse_exists:
            nse_df = fetch_nse(current)
            if nse_df is not None:
                n = write_to_r2(nse_df, "nse_bhavcopy", current)
                total_nse += n
                logger.info("  NSE R2: %d rows", n)
                if load_to_neon:
                    write_to_neon(nse_df, "NSE")
            time.sleep(0.5)

        if not bse_exists:
            bse_df = fetch_bse(current)
            if bse_df is not None:
                n = write_to_r2(bse_df, "bse_eod", current)
                total_bse += n
                logger.info("  BSE R2: %d rows", n)
                if load_to_neon:
                    write_to_neon(bse_df, "BSE")
            time.sleep(1.0)

        total_days += 1
        current    += timedelta(days=1)

        if total_days % 50 == 0:
            logger.info("Progress: %d days — NSE: %d BSE: %d", total_days, total_nse, total_bse)

    logger.info("Done: %d days, NSE: %d, BSE: %d", total_days, total_nse, total_bse)


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
