"""investMITRA — Fetch Financial Data via yfinance v3 (fixed)"""
from __future__ import annotations
import logging, os, time
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
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("SELECT isin, nse_symbol FROM investmitra.company_master WHERE is_active=TRUE AND nse_symbol IS NOT NULL ORDER BY isin")
    rows = cur.fetchall()
    cur.close(); conn.close()
    logger.info("Loaded %d symbols", len(rows))
    return rows


def safe_cr(df, key, period):
    """Safely get value from dataframe in crore."""
    try:
        if key in df.index and period in df.columns:
            v = df.at[key, period]
            if pd.notna(v):
                return round(float(v) / 10000000, 4)
    except:
        pass
    return None


def fetch_yfinance(isin: str, symbol: str) -> list[dict]:
    ticker = f"{symbol}.NS"
    try:
        t   = yf.Ticker(ticker)
        fin = t.quarterly_financials
        bs  = t.quarterly_balance_sheet

        if fin.empty and bs.empty:
            return []

        # Collect all unique periods
        periods = set()
        if not fin.empty: periods.update(fin.columns.tolist())
        if not bs.empty:  periods.update(bs.columns.tolist())

        records = []
        for period in sorted(periods, reverse=True)[:8]:  # last 8 quarters
            period_date = period.date() if hasattr(period, 'date') else None
            if not period_date:
                continue

            revenue    = safe_cr(fin, "Total Revenue",    period)
            ebitda     = safe_cr(fin, "EBITDA",           period)
            ebit       = safe_cr(fin, "Operating Income", period)
            pat        = safe_cr(fin, "Net Income",       period)
            total_debt = safe_cr(bs,  "Total Debt",       period)
            cash       = safe_cr(bs,  "Cash And Cash Equivalents", period)
            equity     = safe_cr(bs,  "Common Stock Equity", period)

            # Skip if no useful data
            if not any([revenue, ebitda, ebit, pat, total_debt, cash, equity]):
                continue

            records.append({
                "isin":          isin,
                "period_end":    period_date,
                "period_type":   "Q",
                "filing_date":   period_date,
                "revenue_cr":    revenue,
                "ebitda_cr":     ebitda,
                "ebit_cr":       ebit,
                "pat_cr":        pat,
                "total_debt_cr": total_debt,
                "cash_cr":       cash,
                "equity_cr":     equity,
            })

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
        (r["isin"], r["period_end"], r["period_type"], r["filing_date"],
         r.get("revenue_cr"), r.get("ebitda_cr"), r.get("ebit_cr"), r.get("pat_cr"),
         None, None,
         r.get("total_debt_cr"),
         r.get("cash_cr"),
         r.get("equity_cr"),
         None, None, None,
         True, None, 80, "yfinance", None)
        for r in records
    ]

    execute_values(cur, """
        INSERT INTO investmitra.company_financials
            (isin, period_end, period_type, filing_date,
             revenue_cr, ebitda_cr, ebit_cr, pat_cr,
             eps, total_assets_cr, total_debt_cr,
             cash_cr, equity_cr,
             cfo_cr, capex_cr, fcf_cr,
             is_consolidated, taxonomy, quality_score, source_id, source_doc_url)
        VALUES %s
        ON CONFLICT (isin, period_end, period_type, COALESCE(source_id, 'unknown')) DO UPDATE SET
            revenue_cr    = COALESCE(EXCLUDED.revenue_cr,    company_financials.revenue_cr),
            ebitda_cr     = COALESCE(EXCLUDED.ebitda_cr,     company_financials.ebitda_cr),
            ebit_cr       = COALESCE(EXCLUDED.ebit_cr,       company_financials.ebit_cr),
            pat_cr        = COALESCE(EXCLUDED.pat_cr,        company_financials.pat_cr),
            total_debt_cr = COALESCE(EXCLUDED.total_debt_cr, company_financials.total_debt_cr),
            cash_cr       = COALESCE(EXCLUDED.cash_cr,       company_financials.cash_cr),
            equity_cr     = COALESCE(EXCLUDED.equity_cr,     company_financials.equity_cr)
    """, rows, page_size=100)

    conn.commit(); cur.close(); conn.close()
    return len(rows)


def main():
    symbols       = get_nse_symbols()
    total_records = 0
    failed        = 0

    logger.info("Fetching financials for %d stocks...", len(symbols))

    # Test with first stock
    isin, symbol = symbols[0]
    test_records = fetch_yfinance(isin, symbol)
    logger.info("Test %s: %d records", symbol, len(test_records))
    if test_records:
        logger.info("Sample: %s", test_records[0])
    else:
        logger.error("No records for test stock — check yfinance")
        return

    for i, (isin, symbol) in enumerate(symbols):
        try:
            records = fetch_yfinance(isin, symbol)
            if records:
                written        = write_to_neon(records)
                total_records += written
            else:
                failed += 1
            if i % 100 == 0 and i > 0:
                logger.info("Progress: %d/%d — records: %d failed: %d",
                            i, len(symbols), total_records, failed)
            time.sleep(0.3)
        except Exception as e:
            logger.warning("Error %s: %s", symbol, e)
            failed += 1

    logger.info("Done: %d records written, %d failed", total_records, failed)

    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT isin) FROM investmitra.company_financials")
    rows, isins = cur.fetchone()
    cur.close(); conn.close()
    print(f"\ncompany_financials: {rows} records, {isins} unique ISINs")


if __name__ == "__main__":
    main()
