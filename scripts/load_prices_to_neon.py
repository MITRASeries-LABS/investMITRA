"""
investMITRA — R2 to Neon: Equity Prices Loader
Reads Parquet files from Cloudflare R2 and loads into Neon equity_prices table.
Includes ISIN enrichment for NSE full file (sec_bhavdata_full) which lacks ISIN column.

Run daily after market data pipeline completes:
  python scripts/load_prices_to_neon.py              # today
  python scripts/load_prices_to_neon.py --date 2026-08-04  # specific date
  python scripts/load_prices_to_neon.py --backfill        # all available dates in R2
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import date, datetime, timedelta, timezone

import duckdb
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NEON_URL     = os.getenv("CC_POSTGRES_URL")
AWS_ENDPOINT = os.getenv("AWS_ENDPOINT_URL")
AWS_KEY      = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET   = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET       = os.getenv("CC_BUCKET_RAW", "cc-raw")
ENV          = os.getenv("CC_ENV", "prod")
IST          = timezone(timedelta(hours=5, minutes=30))

# Cache symbol->ISIN map for the session
_SYMBOL_TO_ISIN: dict[str, str] = {}


def get_duckdb_con() -> duckdb.DuckDBPyConnection:
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


def get_symbol_to_isin() -> dict[str, str]:
    """Load NSE symbol -> ISIN mapping from company_master (cached)."""
    global _SYMBOL_TO_ISIN
    if _SYMBOL_TO_ISIN:
        return _SYMBOL_TO_ISIN
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=15)
        cur  = conn.cursor()
        cur.execute(
            "SELECT nse_symbol, isin FROM investmitra.company_master WHERE nse_symbol IS NOT NULL AND isin IS NOT NULL"
        )
        _SYMBOL_TO_ISIN = {row[0]: row[1] for row in cur.fetchall()}
        cur.close()
        conn.close()
        logger.info("Loaded %d symbol->ISIN mappings from company_master", len(_SYMBOL_TO_ISIN))
    except Exception as e:
        logger.error("Could not load symbol->ISIN map: %s", e)
    return _SYMBOL_TO_ISIN


def load_date(target_date: date) -> dict:
    logger.info("Loading equity prices for %s", target_date)
    con = get_duckdb_con()
    results = {"date": str(target_date), "nse_rows": 0, "bse_rows": 0, "errors": []}

    for source in ["nse_bhavcopy", "bse_eod"]:
        path = (
            f"s3://{BUCKET}/{ENV}/market_data/equity_prices"
            f"/year={target_date.year}"
            f"/month={target_date.month:02d}"
            f"/day={target_date.day:02d}"
            f"/{source}_*.parquet"
        )
        try:
            df = con.execute(
                f"SELECT * FROM read_parquet('{path}', union_by_name=true)"
            ).df()
            logger.info("  %s: %d rows from R2", source, len(df))
            if df.empty:
                continue
            written = write_to_neon(df, target_date, source)
            if source == "nse_bhavcopy":
                results["nse_rows"] = written
            else:
                results["bse_rows"] = written
        except Exception as e:
            logger.warning("  No data for %s — %s", source, e)
            results["errors"].append(str(e))

    con.close()
    logger.info("Loaded %s — NSE: %d BSE: %d", target_date, results["nse_rows"], results["bse_rows"])
    return results


def write_to_neon(df: pd.DataFrame, trade_date: date, source_id: str) -> int:
    """Bulk upsert equity prices into Neon. Uses execute_values for speed."""
    source = "NSE" if "nse" in source_id else "BSE"

    required = ["open", "high", "low", "close", "volume"]
    missing  = [c for c in required if c not in df.columns]
    if missing:
        logger.warning("Missing columns %s in %s", missing, source_id)
        return 0

    # ISIN enrichment for NSE full file (sec_bhavdata_full has no ISIN column)
    if source == "NSE" and "nse_symbol" in df.columns:
        isin_col = df.get("isin", pd.Series(dtype=str))
        if isin_col.isna().all() or "isin" not in df.columns:
            symbol_map = get_symbol_to_isin()
            df = df.copy()
            df["isin"] = df["nse_symbol"].map(symbol_map)
            matched = df["isin"].notna().sum()
            logger.info("  ISIN enrichment: %d/%d symbols matched", matched, len(df))

    # Filter valid ISINs
    if "isin" in df.columns:
        df = df[df["isin"].notna() & (df["isin"].astype(str).str.len() == 12)].copy()

    if df.empty:
        logger.warning("  No valid rows after ISIN filter for %s", source_id)
        return 0

    def safe_float(val):
        try:
            f = float(val)
            return None if (f != f) else f
        except:
            return None

    def safe_int(val):
        try:
            return int(val) if pd.notna(val) else None
        except:
            return None

    rows = []
    for _, row in df.iterrows():
        isin = str(row.get("isin", "")).strip()
        if not isin or len(isin) != 12:
            continue
        rows.append((
            isin,
            trade_date,
            source,
            safe_float(row.get("open")),
            safe_float(row.get("high")),
            safe_float(row.get("low")),
            safe_float(row.get("close")),
            safe_float(row.get("vwap")),
            safe_int(row.get("volume")),
            safe_float(row.get("turnover_cr")),
            safe_float(row.get("delivery_pct")),
            int(row.get("quality_score", 100)),
        ))

    if not rows:
        logger.warning("  No valid rows to insert for %s", source_id)
        return 0

    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
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
        ON CONFLICT (isin, trade_date, source) DO UPDATE SET
            open         = EXCLUDED.open,
            high         = EXCLUDED.high,
            low          = EXCLUDED.low,
            close        = EXCLUDED.close,
            vwap         = EXCLUDED.vwap,
            volume       = EXCLUDED.volume,
            turnover_cr  = EXCLUDED.turnover_cr,
            delivery_pct = EXCLUDED.delivery_pct,
            quality_score= EXCLUDED.quality_score
        """,
        rows,
        template="(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())",
        page_size=500
    )

    conn.commit()
    cur.close()
    conn.close()
    logger.info("  Written %d rows to Neon (%s)", len(rows), source)
    return len(rows)


def list_available_dates() -> list[date]:
    con = get_duckdb_con()
    try:
        path = f"s3://{BUCKET}/{ENV}/market_data/equity_prices/*/*/*/*.parquet"
        df   = con.execute(
            f"SELECT DISTINCT year, month, day FROM read_parquet('{path}', hive_partitioning=true, union_by_name=true)"
        ).df()
        dates = []
        for _, row in df.iterrows():
            try:
                dates.append(date(int(row["year"]), int(row["month"]), int(row["day"])))
            except:
                pass
        con.close()
        return sorted(dates)
    except Exception as e:
        logger.warning("Could not list dates: %s", e)
        con.close()
        return []


def verify():
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("""
        SELECT trade_date, source, COUNT(*) AS stocks,
               ROUND(AVG(close)::numeric, 2) AS avg_close,
               MIN(close) AS min_close, MAX(close) AS max_close
        FROM investmitra.equity_prices
        GROUP BY trade_date, source
        ORDER BY trade_date DESC, source
        LIMIT 10
    """)
    rows = cur.fetchall()
    print(f"\nEquity prices in Neon:")
    print(f"{'Date':<12} {'Src':<5} {'Stocks':>7} {'Avg Close':>12} {'Min':>10} {'Max':>12}")
    print("-" * 65)
    for r in rows:
        print(f"{str(r[0]):<12} {r[1]:<5} {r[2]:>7} {float(r[3] or 0):>12.2f} "
              f"{float(r[4] or 0):>10.2f} {float(r[5] or 0):>12.2f}")
    cur.execute("SELECT COUNT(*) FROM investmitra.equity_prices")
    total = cur.fetchone()[0]
    print(f"\nTotal rows: {total}")
    cur.close()
    conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date",     type=date.fromisoformat)
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--verify",   action="store_true")
    args = parser.parse_args()

    if args.verify:
        verify()
        return

    if args.backfill:
        dates = list_available_dates()
        if not dates:
            logger.warning("No dates found in R2")
            return
        logger.info("Backfilling %d dates: %s to %s", len(dates), dates[0], dates[-1])
        for d in dates:
            load_date(d)
        verify()
        return

    target = args.date or datetime.now(IST).date()
    load_date(target)
    verify()


if __name__ == "__main__":
    main()
