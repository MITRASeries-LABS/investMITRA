"""
investMITRA — Company Master Enrichment v2
Uses yfinance for sector/industry. NSE API for BSE codes.
Run: python scripts/enrich_company_master.py [--sectors-only] [--limit N] [--verify]
"""

from __future__ import annotations
import io, logging, os, time
import pandas as pd
import psycopg2
import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv('.env.prod')
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
NEON_URL = os.getenv("CC_POSTGRES_URL")

NSE_INDICES = {
    "LARGE": ["https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv"],
    "MID":   ["https://nsearchives.nseindia.com/content/indices/ind_niftymidcap150list.csv"],
    "SMALL": ["https://nsearchives.nseindia.com/content/indices/ind_niftysmallcap250list.csv"],
}

def get_symbols_from_db():
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur = conn.cursor()
    cur.execute("SELECT isin, nse_symbol, bse_code, sector FROM investmitra.company_master WHERE is_active=TRUE AND nse_symbol IS NOT NULL ORDER BY isin")
    rows = cur.fetchall(); cur.close(); conn.close()
    logger.info("Loaded %d symbols", len(rows))
    return rows

def fetch_cap_categories():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com"})
    session.get("https://www.nseindia.com", timeout=15); time.sleep(1)
    sym_cat = {}
    for cat, urls in NSE_INDICES.items():
        for url in urls:
            try:
                df = pd.read_csv(io.StringIO(session.get(url, timeout=20).text), dtype=str)
                df.columns = df.columns.str.strip()
                col = next((c for c in df.columns if "symbol" in c.lower()), None)
                if col:
                    for s in df[col].dropna():
                        if s.strip() not in sym_cat: sym_cat[s.strip()] = cat
                logger.info("%s: %d from %s", cat, len(df), url.split("/")[-1])
                time.sleep(0.5)
            except Exception as e:
                logger.warning("Cap fetch failed %s: %s", url, e)
    return sym_cat

def fetch_sectors(symbols, batch=50):
    logger.info("Fetching sectors via yfinance for %d symbols...", len(symbols))
    result = {}
    for i in range(0, len(symbols), batch):
        chunk = symbols[i:i+batch]
        tickers = " ".join(f"{s}.NS" for s in chunk)
        try:
            data = yf.Tickers(tickers)
            for sym in chunk:
                try:
                    info = data.tickers[f"{sym}.NS"].info
                    result[sym] = {"sector": info.get("sector"), "industry": info.get("industry")}
                except Exception:
                    result[sym] = {"sector": None, "industry": None}
        except Exception as e:
            logger.warning("yfinance batch failed: %s", e)
            for sym in chunk: result[sym] = {"sector": None, "industry": None}
        logger.info("Sectors progress: %d/%d", min(i+batch, len(symbols)), len(symbols))
        time.sleep(2)
    filled = sum(1 for v in result.values() if v.get("sector"))
    logger.info("Sectors: %d/%d filled", filled, len(result))
    return result

def fetch_bse_codes(symbols):
    logger.info("Fetching BSE codes from NSE for %d symbols...", len(symbols))
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com", "Accept": "application/json"})
    session.get("https://www.nseindia.com", timeout=15); time.sleep(2)
    result = {}
    for i, sym in enumerate(symbols):
        try:
            resp = session.get(f"https://www.nseindia.com/api/quote-equity?symbol={sym}", timeout=10)
            if resp.status_code == 200 and resp.text.strip():
                data = resp.json()
                bse = str(data.get("securityInfo", {}).get("scripCode", "") or "").strip()
                if bse and bse != "0": result[sym] = bse.zfill(6)
            if i % 100 == 0 and i > 0:
                logger.info("BSE codes: %d/%d (found: %d)", i, len(symbols), len(result))
                session.get("https://www.nseindia.com", timeout=10); time.sleep(1)
            time.sleep(0.3)
        except Exception as e:
            logger.debug("BSE code failed %s: %s", sym, e)
    logger.info("BSE codes found: %d/%d", len(result), len(symbols))
    return result

def update_db(db_rows, sector_map, cap_map, bse_map):
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = False
    cur = conn.cursor()
    updated = 0
    for isin, nse_sym, existing_bse, existing_sector in db_rows:
        s = sector_map.get(nse_sym, {})
        cur.execute(
            "UPDATE investmitra.company_master SET bse_code=COALESCE(%s,bse_code), sector=COALESCE(%s,sector), industry=COALESCE(%s,industry), market_cap_category=%s, updated_at=NOW() WHERE isin=%s",
            (bse_map.get(nse_sym) or existing_bse, s.get("sector") or existing_sector, s.get("industry"), cap_map.get(nse_sym, "MICRO"), isin)
        )
        updated += 1
    conn.commit(); cur.close(); conn.close()
    return {"updated": updated}

def verify():
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*), COUNT(bse_code), COUNT(sector), COUNT(industry), COUNT(market_cap_category) FROM investmitra.company_master WHERE is_active=TRUE")
    r = cur.fetchone()
    print(f"\nCompany master enrichment:")
    print(f"  Total: {r[0]}  BSE codes: {r[1]}  Sector: {r[2]}  Industry: {r[3]}  Cap cat: {r[4]}")
    cur.execute("SELECT market_cap_category, COUNT(*) FROM investmitra.company_master WHERE is_active=TRUE GROUP BY 1 ORDER BY 2 DESC")
    print("\n  Cap categories:")
    for cat, cnt in cur.fetchall(): print(f"    {cat or 'NULL'}: {cnt}")
    cur.execute("SELECT sector, COUNT(*) FROM investmitra.company_master WHERE is_active=TRUE AND sector IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 10")
    print("\n  Top sectors:")
    for sec, cnt in cur.fetchall(): print(f"    {sec}: {cnt}")
    cur.close(); conn.close()

def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--verify",       action="store_true")
    p.add_argument("--sectors-only", action="store_true")
    p.add_argument("--limit",        type=int, default=0)
    args = p.parse_args()

    if args.verify:
        verify(); return

    db_rows = get_symbols_from_db()
    if args.limit:
        db_rows = db_rows[:args.limit]
    symbols = [r[1] for r in db_rows]

    cap_map    = fetch_cap_categories()
    sector_map = fetch_sectors(symbols)
    bse_map    = {} if args.sectors_only else fetch_bse_codes(symbols)

    result = update_db(db_rows, sector_map, cap_map, bse_map)
    logger.info("Done: %s", result)
    verify()

if __name__ == "__main__":
    main()
