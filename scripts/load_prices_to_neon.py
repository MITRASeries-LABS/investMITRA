"""
investMITRA — R2 to Neon: Equity Prices Loader
Reads Parquet files from Cloudflare R2 and loads into Neon equity_prices table.

Run daily after market data pipeline completes:
  python scripts/load_prices_to_neon.py              # today
  python scripts/load_prices_to_neon.py --date 2026-08-04  # specific date
  python scripts/load_prices_to_neon.py --backfill        # all available dates in R2
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import date, datetime, timedelta, timezone, timedelta

import duckdb
import pandas as pd
import psycopg2
from dotenv import load_dotenv

load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NEON_URL        = os.getenv("CC_POSTGRES_URL")
AWS_ENDPOINT    = os.getenv("AWS_ENDPOINT_URL")
AWS_KEY         = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET      = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET          = os.getenv("CC_BUCKET_RAW", "cc-raw")
ENV             = os.getenv("CC_ENV", "prod")
IST             = timezone(timedelta(hours=5, minutes=30))


def get_duckdb_con() -> duckdb.DuckDBPyConnection:
    """DuckDB connection with R2/S3 credentials configured."""
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


def load_date(target_date: date) -> dict:
    """Load equity prices for a single date from R2 into Neon."""
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
            df = con.execute(f"SELECT * FROM read_parquet('{path}')").df()
            logger.info("  %s: %d rows from R2", source, len(df))

            if df.empty:
                continue

            written = write_to_neon(df, target_date, source)
            if source == "nse_bhavcopy":
                results["nse_rows"] = written
            else:
                results["bse_rows"] = written

        except Exception as e:
            msg = f"{source}: {e}"
            logger.warning("  No data for %s — %s", source, e)
            results["errors"].append(msg)

    con.close()
    logger.info("Loaded %s — NSE: %d BSE: %d",
                target_date, results["nse_rows"], results["bse_rows"])
    return results


def write_to_neon(df: pd.DataFrame, trade_date: date, source_id: str) -> int:
    """Upsert equity prices into Neon equity_prices table."""
    source = "NSE" if "nse" in source_id else "BSE"

    # Ensure required columns exist
    required = ["isin", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        logger.warning("Missing columns %s in %s", missing, source_id)
        return 0

    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = False
    cur = conn.cursor()
    written = 0

    for _, row in df.iterrows():
        isin = str(row.get("isin", "")).strip()
        if not isin or len(isin) != 12:
            continue

        def safe_decimal(val):
            try: return float(val) if pd.notna(val) else None
            except: return None

        def safe_int(val):
            try: return int(val) if pd.notna(val) else None
            except: return None

        try:
            cur.execute(
                """
                INSERT INTO investmitra.equity_prices
                    (isin, trade_date, source,
                     open, high, low, close, vwap,
                     volume, turnover_cr, delivery_pct,
                     quality_score, ingested_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
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
                (
                    isin, trade_date, source,
                    safe_decimal(row.get("open")),
                    safe_decimal(row.get("high")),
                    safe_decimal(row.get("low")),
                    safe_decimal(row.get("close")),
                    safe_decimal(row.get("vwap")),
                    safe_int(row.get("volume")),
                    safe_decimal(row.get("turnover_cr")),
                    safe_decimal(row.get("delivery_pct")),
                    int(row.get("quality_score", 100)),
                )
            )
            written += 1
        except Exception as e:
            logger.debug("Row failed %s: %s", isin, e)

    conn.commit()
    cur.close()
    conn.close()
    logger.info("  Written %d rows to Neon (%s)", written, source)
    return written


def list_available_dates() -> list[date]:
    """List all dates available in R2 for equity prices."""
    con = get_duckdb_con()
    try:
        path = f"s3://{BUCKET}/{ENV}/market_data/equity_prices/*/*/*/*.parquet"
        df = con.execute(
            f"SELECT DISTINCT year, month, day FROM read_parquet('{path}', hive_partitioning=true)"
        ).df()
        dates = []
        for _, row in df.iterrows():
            try:
                dates.append(date(int(row["year"]), int(row["month"]), int(row["day"])))
            except Exception:
                pass
        con.close()
        return sorted(dates)
    except Exception as e:
        logger.warning("Could not list dates: %s", e)
        con.close()
        return []


def verify():
    """Quick verification of what's in Neon equity_prices."""
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur = conn.cursor()

    cur.execute("""
        SELECT
            trade_date, source,
            COUNT(*) AS stocks,
            AVG(close) AS avg_close,
            MIN(close) AS min_close,
            MAX(close) AS max_close
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
    parser.add_argument("--date",     type=date.fromisoformat, help="Specific date YYYY-MM-DD")
    parser.add_argument("--backfill", action="store_true",     help="Load all dates from R2")
    parser.add_argument("--verify",   action="store_true",     help="Show Neon equity_prices stats")
    args = parser.parse_args()

    if args.verify:
        verify()
        return

    if args.backfill:
        dates = list_available_dates()
        if not dates:
            logger.warning("No dates found in R2 — run market data pipeline first")
            return
        logger.info("Backfilling %d dates from R2: %s to %s", len(dates), dates[0], dates[-1])
        for d in dates:
            load_date(d)
        verify()
        return

    # Default: today in IST
    target = args.date or datetime.now(IST).date()
    load_date(target)
    verify()


if __name__ == "__main__":
    main()
