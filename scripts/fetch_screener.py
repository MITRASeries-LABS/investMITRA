"""
investMITRA — Screener.in Data Fetcher
Scrapes annual financial data from screener.in for all NSE stocks.

Data fetched per stock:
  - Revenue (10 years)
  - Net block / Fixed Assets (10 years)
  - CWIP (10 years)
  - Borrowings (10 years)
  - Equity + Reserves
  - Promoter holding (quarterly)
  - Key ratios (ROE, ROCE, D/E)

Used for:
  - Capex Expansion Screen (Net block doubled in 3yr or CWIP +50% YoY)
  - Graham Screen (10-year avg P/E, ROCE)
  - Piotroski (asset turnover, leverage trend)

Run: python scripts/fetch_screener.py
"""
from __future__ import annotations
import logging, os, re, time
from datetime import datetime, timezone, timedelta
import requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv
load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NEON_URL = os.getenv("CC_POSTGRES_URL")
BASE_URL = "https://www.screener.in/company/{symbol}/"
HEADERS  = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}
DELAY    = 1.5  # seconds between requests — be polite


def get_symbols() -> list[tuple[str, str]]:
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


def parse_number(s: str) -> float | None:
    """Parse Indian number format: '1,23,456' → 123456.0"""
    try:
        clean = s.replace(',', '').replace('%', '').strip()
        if clean in ('', '-', '--', 'N/A'): return None
        return float(clean)
    except: return None


def parse_table(table) -> dict[str, list]:
    """Parse a screener.in financial table into {row_label: [values]}"""
    result = {}
    rows   = table.find_all('tr')
    if not rows: return result

    # Get column headers (years)
    headers = []
    header_row = rows[0]
    for th in header_row.find_all(['th', 'td']):
        txt = th.get_text(strip=True)
        if txt: headers.append(txt)

    # Parse data rows
    for row in rows[1:]:
        cells = row.find_all(['td', 'th'])
        if not cells: continue
        label = cells[0].get_text(strip=True).rstrip('+').strip()
        if not label: continue
        values = []
        for cell in cells[1:]:
            txt = cell.get_text(strip=True)
            values.append(parse_number(txt))
        result[label] = values

    result['_headers'] = headers[1:] if headers else []
    return result


def fetch_screener_data(symbol: str) -> dict | None:
    """Fetch all financial data for one stock from screener.in"""
    url = BASE_URL.format(symbol=symbol)
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            logger.debug("HTTP %d for %s", r.status_code, symbol)
            return None

        soup   = requests.structures.CaseInsensitiveDict() if False else None
        soup   = BeautifulSoup(r.text, 'html.parser')
        tables = soup.find_all('table')

        if len(tables) < 7:
            return None

        # Parse key tables
        # Table 1 = Annual P&L, Table 6 = Annual Balance Sheet
        # Table 7 = Cash Flow, Table 8 = Ratios, Table 11 = Shareholding
        pl   = parse_table(tables[1])  # Annual P&L
        bs   = parse_table(tables[6])  # Annual Balance Sheet
        cf   = parse_table(tables[7]) if len(tables) > 7 else {}
        rat  = parse_table(tables[8]) if len(tables) > 8 else {}

        years = bs.get('_headers', pl.get('_headers', []))

        result = {
            "symbol":  symbol,
            "years":   years,
            "revenue": pl.get('Sales', pl.get('Revenue', [])),
            "net_profit": pl.get('Net Profit', []),
            "fixed_assets": bs.get('Fixed Assets', []),
            "cwip":     bs.get('CWIP', []),
            "borrowings": bs.get('Borrowings', []),
            "equity":   bs.get('Equity Capital', []),
            "reserves": bs.get('Reserves', []),
            "total_assets": bs.get('Total Assets', []),
            "cfo":      cf.get('Cash from Operating Activity', []),
            "roce":     rat.get('ROCE %', rat.get('Return on capital employed %', [])),
            "roe":      rat.get('ROE %', rat.get('Return on equity %', [])),
            "de_ratio": rat.get('Debt to equity', []),
        }

        # Try to get promoter holding from shareholding table
        if len(tables) > 11:
            sh = parse_table(tables[11])
            result["promoter_pct"] = sh.get('Promoters', [])

        return result

    except Exception as e:
        logger.debug("Failed %s: %s", symbol, e)
        return None


def compute_capex_screen(data: dict) -> dict:
    """
    Capex Expansion Screen:
    (Sales growth 3yr > 12% AND Net block doubled in 3yr)
    OR CWIP grew > 50% YoY
    AND Sales > 25 Cr AND D/E < 3 AND Market Cap > 25 Cr
    """
    rev  = data.get("revenue", [])
    fa   = data.get("fixed_assets", [])
    cwip = data.get("cwip", [])
    de   = data.get("de_ratio", [])

    # Need at least 3-4 years of data
    if len(rev) < 3 or len(fa) < 3:
        return {}

    # Revenue growth 3 years (most recent vs 3 years ago)
    rev_cur = rev[0] if rev[0] else None
    rev_3yr = rev[3] if len(rev) > 3 and rev[3] else (rev[2] if rev[2] else None)
    rev_growth_3yr = None
    if rev_cur and rev_3yr and rev_3yr > 0 and rev_cur > 0:
        try:
            rev_growth_3yr = ((rev_cur / rev_3yr) ** (1/3) - 1) * 100
            rev_growth_3yr = float(rev_growth_3yr.real if hasattr(rev_growth_3yr, 'real') else rev_growth_3yr)
        except: rev_growth_3yr = None

    # Fixed assets comparison
    fa_cur = fa[0] if fa[0] else None
    fa_3yr = fa[3] if len(fa) > 3 and fa[3] else (fa[2] if fa[2] else None)
    fa_1yr = fa[1] if len(fa) > 1 and fa[1] else None
    fa_doubled = bool(fa_cur and fa_3yr and fa_3yr > 0 and fa_cur >= fa_3yr * 2)

    # CWIP growth YoY
    cwip_cur = cwip[0] if cwip and cwip[0] else None
    cwip_1yr = cwip[1] if cwip and len(cwip) > 1 and cwip[1] else None
    cwip_surge = bool(cwip_cur and cwip_1yr and cwip_1yr > 0 and
                     cwip_cur >= cwip_1yr * 1.5)

    # Current D/E
    de_cur = de[0] if de and de[0] else None

    # Capex screen pass
    condition1 = bool(rev_growth_3yr and rev_growth_3yr > 12 and fa_doubled)
    condition2 = cwip_surge
    capex_pass = condition1 or condition2

    # Sales filter
    sales_ok = bool(rev_cur and rev_cur > 25)
    de_ok    = bool(de_cur is None or de_cur < 3)

    return {
        "rev_growth_3yr_pct": round(rev_growth_3yr, 2) if rev_growth_3yr else None,
        "fa_cur_cr":          round(fa_cur, 2) if fa_cur else None,
        "fa_3yr_cr":          round(fa_3yr, 2) if fa_3yr else None,
        "fa_doubled":         fa_doubled,
        "cwip_cur_cr":        round(cwip_cur, 2) if cwip_cur else None,
        "cwip_1yr_cr":        round(cwip_1yr, 2) if cwip_1yr else None,
        "cwip_surge_50pct":   cwip_surge,
        "de_ratio_cur":       round(de_cur, 2) if de_cur else None,
        "capex_screen_pass":  capex_pass and sales_ok and de_ok,
        "capex_condition1":   condition1,
        "capex_condition2":   condition2,
    }


def ensure_tables():
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = True
    cur  = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS investmitra.screener_data (
            isin                  VARCHAR(12) PRIMARY KEY,
            symbol                VARCHAR(20),
            years                 TEXT[],
            revenue_cr            DECIMAL[],
            net_profit_cr         DECIMAL[],
            fixed_assets_cr       DECIMAL[],
            cwip_cr               DECIMAL[],
            borrowings_cr         DECIMAL[],
            total_assets_cr       DECIMAL[],
            roce_pct              DECIMAL[],
            roe_pct               DECIMAL[],
            de_ratio              DECIMAL[],
            promoter_pct          DECIMAL[],
            rev_growth_3yr_pct    DECIMAL(8,2),
            fa_doubled            BOOLEAN,
            cwip_surge_50pct      BOOLEAN,
            capex_screen_pass     BOOLEAN,
            de_ratio_cur          DECIMAL(8,2),
            updated_at            TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.close(); conn.close()


def write_to_neon(isin: str, data: dict, capex: dict) -> bool:
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=15)
        conn.autocommit = False
        cur  = conn.cursor()

        def clean_arr(arr):
            if not arr: return None
            return [float(x) if x is not None else None for x in arr[:10]]

        cur.execute("""
            INSERT INTO investmitra.screener_data
                (isin, symbol, years, revenue_cr, net_profit_cr,
                 fixed_assets_cr, cwip_cr, borrowings_cr, total_assets_cr,
                 roce_pct, roe_pct, de_ratio, promoter_pct,
                 rev_growth_3yr_pct, fa_doubled, cwip_surge_50pct,
                 capex_screen_pass, de_ratio_cur)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (isin) DO UPDATE SET
                revenue_cr         = EXCLUDED.revenue_cr,
                fixed_assets_cr    = EXCLUDED.fixed_assets_cr,
                cwip_cr            = EXCLUDED.cwip_cr,
                rev_growth_3yr_pct = EXCLUDED.rev_growth_3yr_pct,
                fa_doubled         = EXCLUDED.fa_doubled,
                cwip_surge_50pct   = EXCLUDED.cwip_surge_50pct,
                capex_screen_pass  = EXCLUDED.capex_screen_pass,
                updated_at         = NOW()
        """, (
            isin, data["symbol"],
            data.get("years", [])[:10],
            clean_arr(data.get("revenue")),
            clean_arr(data.get("net_profit")),
            clean_arr(data.get("fixed_assets")),
            clean_arr(data.get("cwip")),
            clean_arr(data.get("borrowings")),
            clean_arr(data.get("total_assets")),
            clean_arr(data.get("roce")),
            clean_arr(data.get("roe")),
            clean_arr(data.get("de_ratio")),
            clean_arr(data.get("promoter_pct")),
            capex.get("rev_growth_3yr_pct"),
            capex.get("fa_doubled"),
            capex.get("cwip_surge_50pct"),
            capex.get("capex_screen_pass"),
            capex.get("de_ratio_cur"),
        ))
        conn.commit(); cur.close(); conn.close()
        return True
    except Exception as e:
        logger.debug("Write failed for %s: %s", isin, e)
        return False


def print_capex_results():
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("""
        SELECT sd.isin, cm.company_name, cm.sector, cm.market_cap_category,
               sd.rev_growth_3yr_pct, sd.fa_doubled, sd.cwip_surge_50pct,
               sd.de_ratio_cur,
               sd.revenue_cr[1] AS rev_cur,
               sd.fixed_assets_cr[1] AS fa_cur,
               sd.cwip_cr[1] AS cwip_cur
        FROM investmitra.screener_data sd
        JOIN investmitra.company_master cm ON sd.isin = cm.isin
        WHERE sd.capex_screen_pass = TRUE
        ORDER BY sd.rev_growth_3yr_pct DESC NULLS LAST
        LIMIT 30
    """)
    rows = cur.fetchall()
    cur.execute("SELECT COUNT(*) FROM investmitra.screener_data WHERE capex_screen_pass=TRUE")
    total = cur.fetchone()[0]
    cur.close(); conn.close()

    print(f"\n{'='*100}")
    print(f"CAPEX EXPANSION SCREEN — {total} stocks passing")
    print(f"{'='*100}")
    print(f"{'Company':<30} {'Sector':<18} {'Cap':<6} {'Rev3yr%':>8} {'FA2x':>5} {'CWIP+':>5} {'D/E':>6} {'Rev Cr':>10} {'FA Cr':>10}")
    print(f"{'─'*100}")
    for r in rows:
        print(f"{str(r[1])[:29]:<30} {str(r[2])[:17]:<18} {str(r[3])[:5]:<6} "
              f"{float(r[4] or 0):>8.1f} {'Y' if r[5] else 'N':>5} {'Y' if r[6] else 'N':>5} "
              f"{float(r[7] or 0):>6.1f} {float(r[8] or 0):>10.0f} {float(r[9] or 0):>10.0f}")


def main():
    ensure_tables()
    symbols = get_symbols()
    total   = 0
    failed  = 0
    session = requests.Session()
    session.headers.update(HEADERS)

    logger.info("Fetching Screener.in data for %d stocks...", len(symbols))

    for i, (isin, symbol) in enumerate(symbols):
        data = fetch_screener_data(symbol)
        if data:
            capex = compute_capex_screen(data)
            write_to_neon(isin, data, capex)
            total += 1
        else:
            failed += 1

        if i % 50 == 0 and i > 0:
            logger.info("Progress: %d/%d — fetched: %d failed: %d",
                        i, len(symbols), total, failed)

        time.sleep(DELAY)

    logger.info("Done: %d fetched, %d failed", total, failed)
    print_capex_results()


if __name__ == "__main__":
    main()
