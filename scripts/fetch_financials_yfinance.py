"""
investMITRA — Fetch Financial Data via yfinance
Loads quarterly financials for all NSE stocks into company_financials table.

Run: python scripts/fetch_financials_yfinance.py
"""
from __future__ import annotations
import logging, os, time
from datetime import datetime, timezone, timedelta
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
import yfinance as yf
from dotenv import load_dotenv
load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
NEON_URL = os.getenv("CC_POSTGRES_URL")


def get_nse_symbols() -> list[tuple[str, str]]:
    """Get all active ISIN, NSE symbol pairs from company_master."""
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute(
        "SELECT isin, nse_symbol FROM investmitra.company_master "
        "WHERE is_active=TRUE AND nse_symbol IS NOT NULL ORDER BY isin"
    )
    rows = cur.fetchall()
    cur.close(); conn.close()
    logger.info("Loaded %d symbols", len(rows))
    return rows


def fetch_yfinance(isin: str, symbol: str) -> list[dict]:
    """Fetch quarterly financials for one stock."""
    ticker = f"{symbol}.NS"
    try:
        t   = yf.Ticker(ticker)
        fin = t.quarterly_financials
        bs  = t.quarterly_balance_sheet

        if fin.empty and bs.empty:
            return []

        records = []
        # Get all available quarters
        periods = set()
        if not fin.empty:
            periods.update(fin.columns.tolist())
        if not bs.empty:
            periods.update(bs.columns.tolist())

        for period in periods:
            def get_val(df, key):
                try:
                    if key in df.index and period in df.columns:
                        v = df.loc[key, period]
                        return float(v) / 10000000 if pd.notna(v) else None  # Convert to crore
                except: pass
                return None

            rec = {
                "isin":           isin,
                "period_end":     period.date() if hasattr(period, 'date') else None,
                "period_type":    "Q",
                "revenue_cr":     get_val(fin, "Total Revenue"),
                "ebitda_cr":      get_val(fin, "EBITDA"),
                "ebit_cr":        get_val(fin, "EBIT"),
                "pat_cr":         get_val(fin, "Net Income From Continuing Operation Net Minority Interest"),
                "total_debt_cr":  get_val(bs, "Total Debt"),
                "cash_cr":        get_val(bs, "Cash And Cash Equivalents"),
                "equity_cr":      get_val(bs, "Common Stock Equity"),
                "source_id":      "yfinance",
                "quality_score":  80,
            }

            if rec["period_end"] and any(v is not None for k, v in rec.items()
                                          if k not in ["isin","period_end","period_type","source_id","quality_score"]):
                records.append(rec)

        return records

    except Exception as e:
        logger.debug("Failed %s: %s", symbol, e)
        return []


def write_to_neon(records: list[dict]) -> int:
    if not records: return 0
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = False
    cur  = conn.cursor()

    rows = [
        (r["isin"], r["period_end"], r["period_type"], None,
         r.get("revenue_cr"), r.get("ebitda_cr"), r.get("ebit_cr"), r.get("pat_cr"),
         None, None, None, r.get("total_debt_cr"), r.get("cash_cr"), r.get("equity_cr"),
         None, None, None, True, None, r.get("quality_score", 80), r.get("source_id"), None)
        for r in records if r.get("period_end")
    ]

    execute_values(cur, """
        INSERT INTO investmitra.company_financials
            (isin, period_end, period_type, filing_date,
             revenue_cr, ebitda_cr, ebit_cr, pat_cr,
             eps, total_assets_cr, total_debt_cr, total_debt_cr,
             cash_cr, equity_cr, cfo_cr, capex_cr, fcf_cr,
             is_consolidated, taxonomy, quality_score, source_id, source_doc_url)
        VALUES %s
        ON CONFLICT DO NOTHING
    """, rows, page_size=100)

    conn.commit(); cur.close(); conn.close()
    return len(rows)


def main():
    symbols = get_nse_symbols()
    total_records = 0
    failed = 0

    logger.info("Fetching financials for %d stocks via yfinance...", len(symbols))

    for i, (isin, symbol) in enumerate(symbols):
        try:
            records = fetch_yfinance(isin, symbol)
            if records:
                written = write_to_neon(records)
                total_records += written
                if i % 50 == 0:
                    logger.info("Progress: %d/%d — records: %d failed: %d",
                                i, len(symbols), total_records, failed)
            time.sleep(0.3)
        except Exception as e:
            logger.debug("Error %s: %s", symbol, e)
            failed += 1

    logger.info("Done: %d records written, %d failed", total_records, failed)

    # Verify
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT isin) FROM investmitra.company_financials")
    rows, isins = cur.fetchone()
    cur.close(); conn.close()
    print(f"\ncompany_financials: {rows} records, {isins} unique ISINs")


if __name__ == "__main__":
    main()
