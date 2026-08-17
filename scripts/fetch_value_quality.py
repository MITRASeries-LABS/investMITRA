"""
investMITRA — Value Quality Score v3
Benjamin Graham Screen + Piotroski F-Score (9 criteria)
Fixed: numpy.int64 type conversion issue

Run: python scripts/fetch_value_quality.py
"""
from __future__ import annotations
import logging, os, time
import pandas as pd, numpy as np
import psycopg2
from psycopg2.extras import execute_values
import yfinance as yf
from dotenv import load_dotenv
load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
NEON_URL = os.getenv("CC_POSTGRES_URL")


def get_symbols() -> list[tuple[str, str]]:
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("SELECT isin, nse_symbol FROM investmitra.company_master WHERE is_active=TRUE AND nse_symbol IS NOT NULL ORDER BY isin")
    rows = cur.fetchall()
    cur.close(); conn.close()
    logger.info("Loaded %d symbols", len(rows))
    return rows


def sf(v):
    """Safe float conversion."""
    try:
        f = float(v)
        return None if (np.isnan(f) or np.isinf(f)) else round(f, 4)
    except: return None


def si(v):
    """Safe int conversion."""
    try: return int(v)
    except: return 0


def get_val(df, key, col_idx=0):
    try:
        if key in df.index and col_idx < len(df.columns):
            return sf(df.loc[key, df.columns[col_idx]])
    except: pass
    return None


def fetch_and_score(isin: str, symbol: str) -> dict | None:
    try:
        t    = yf.Ticker(f"{symbol}.NS")
        fin  = t.financials
        bs   = t.balance_sheet
        cf   = t.cashflow
        info = t.info or {}

        if fin.empty and bs.empty:
            return None

        # Annual data — col 0 = latest year, col 1 = prior year
        rev_cur   = get_val(fin, "Total Revenue", 0)
        rev_pri   = get_val(fin, "Total Revenue", 1)
        ni_cur    = get_val(fin, "Net Income", 0)
        ni_pri    = get_val(fin, "Net Income", 1)
        ebit_cur  = get_val(fin, "EBIT", 0)
        gross_cur = get_val(fin, "Gross Profit", 0)
        gross_pri = get_val(fin, "Gross Profit", 1)

        asset_cur     = get_val(bs, "Total Assets", 0)
        asset_pri     = get_val(bs, "Total Assets", 1)
        debt_cur      = get_val(bs, "Total Debt", 0)
        debt_pri      = get_val(bs, "Total Debt", 1)
        curr_cur      = get_val(bs, "Current Assets", 0)
        curr_pri      = get_val(bs, "Current Assets", 1)
        curr_lia_cur  = get_val(bs, "Current Liabilities", 0)
        curr_lia_pri  = get_val(bs, "Current Liabilities", 1)
        shares_cur    = get_val(bs, "Ordinary Shares Number", 0)
        shares_pri    = get_val(bs, "Ordinary Shares Number", 1)

        cfo_cur = get_val(cf, "Operating Cash Flow", 0)
        if cfo_cur is None:
            cfo_cur = get_val(cf, "Cash From Operations", 0)

        # ── PIOTROSKI F-SCORE ──────────────────────────────────────────
        roa_cur = (ni_cur / asset_cur) if ni_cur and asset_cur and asset_cur > 0 else None
        roa_pri = (ni_pri / asset_pri) if ni_pri and asset_pri and asset_pri > 0 else None
        lev_cur = (debt_cur / asset_cur) if debt_cur is not None and asset_cur and asset_cur > 0 else None
        lev_pri = (debt_pri / asset_pri) if debt_pri is not None and asset_pri and asset_pri > 0 else None
        cr_cur  = (curr_cur / curr_lia_cur) if curr_cur and curr_lia_cur and curr_lia_cur > 0 else None
        cr_pri  = (curr_pri / curr_lia_pri) if curr_pri and curr_lia_pri and curr_lia_pri > 0 else None
        gm_cur  = (gross_cur / rev_cur) if gross_cur and rev_cur and rev_cur > 0 else None
        gm_pri  = (gross_pri / rev_pri) if gross_pri and rev_pri and rev_pri > 0 else None
        at_cur  = (rev_cur / asset_cur) if rev_cur and asset_cur and asset_cur > 0 else None
        at_pri  = (rev_pri / asset_pri) if rev_pri and asset_pri and asset_pri > 0 else None

        f1 = 1 if (roa_cur and roa_cur > 0) else 0
        f2 = 1 if (cfo_cur and cfo_cur > 0) else 0
        f3 = 1 if (roa_cur and roa_pri and roa_cur > roa_pri) else 0
        f4 = 1 if (cfo_cur and ni_cur and cfo_cur > ni_cur) else 0
        f5 = 1 if (lev_cur is not None and lev_pri is not None and lev_cur < lev_pri) else 0
        f6 = 1 if (cr_cur and cr_pri and cr_cur > cr_pri) else 0
        f7 = 1 if (shares_cur and shares_pri and shares_cur <= shares_pri * 1.02) else 0
        f8 = 1 if (gm_cur and gm_pri and gm_cur > gm_pri) else 0
        f9 = 1 if (at_cur and at_pri and at_cur > at_pri) else 0

        f_score = f1 + f2 + f3 + f4 + f5 + f6 + f7 + f8 + f9

        # ── GRAHAM SCREEN ──────────────────────────────────────────────
        pe        = sf(info.get("trailingPE"))
        payout    = sf(info.get("payoutRatio"))
        de        = sf(info.get("debtToEquity"))
        div_yield = sf(info.get("dividendYield"))
        roe       = sf(info.get("returnOnEquity"))
        roce      = sf(ebit_cur / asset_cur * 100) if ebit_cur and asset_cur and asset_cur > 0 else None

        pe_ok   = bool(pe and 0 < pe < 15)
        div_ok  = bool(payout and payout * 100 > 20)
        de_ok   = bool(de is not None and de < 20)
        roce_ok = bool(roce and roce > 20)

        graham_score = int(pe_ok) + int(div_ok) + int(de_ok) + int(roce_ok)
        graham_pass  = graham_score == 4

        # ── Value Quality Score ────────────────────────────────────────
        piotroski_pct = f_score / 9 * 100
        graham_pct    = graham_score / 4 * 100
        vq_score      = round(piotroski_pct * 0.60 + graham_pct * 0.40, 2)

        return {
            "isin":                isin,
            "piotroski_score":     int(f_score),
            "piotroski_label":     "Strong" if f_score >= 7 else "Neutral" if f_score >= 4 else "Weak",
            "graham_criteria_met": int(graham_score),
            "graham_pass":         bool(graham_pass),
            "value_quality_score": vq_score,
            "trailing_pe":         sf(pe),
            "payout_ratio_pct":    sf(payout * 100) if payout else None,
            "debt_to_equity":      sf(de),
            "roce_pct":            sf(roce),
            "roe_pct":             sf(roe * 100) if roe else None,
            "dividend_yield_pct":  sf(div_yield * 100) if div_yield else None,
            "roa_cur":             sf(roa_cur * 100) if roa_cur else None,
            "current_ratio":       sf(cr_cur),
            "f1": int(f1), "f2": int(f2), "f3": int(f3), "f4": int(f4), "f5": int(f5),
            "f6": int(f6), "f7": int(f7), "f8": int(f8), "f9": int(f9),
        }

    except Exception as e:
        logger.debug("Failed %s: %s", symbol, e)
        return None


def ensure_table():
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = True
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS investmitra.value_quality (
            isin                  VARCHAR(12) PRIMARY KEY,
            piotroski_score       INTEGER,
            piotroski_label       VARCHAR(20),
            graham_criteria_met   INTEGER,
            graham_pass           BOOLEAN,
            value_quality_score   DECIMAL(6,2),
            trailing_pe           DECIMAL(10,2),
            payout_ratio_pct      DECIMAL(8,2),
            debt_to_equity        DECIMAL(10,2),
            roce_pct              DECIMAL(8,2),
            roe_pct               DECIMAL(8,2),
            dividend_yield_pct    DECIMAL(8,4),
            roa_cur               DECIMAL(8,2),
            current_ratio         DECIMAL(8,2),
            f1 INTEGER, f2 INTEGER, f3 INTEGER, f4 INTEGER, f5 INTEGER,
            f6 INTEGER, f7 INTEGER, f8 INTEGER, f9 INTEGER,
            updated_at            TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.close(); conn.close()


def write_to_neon(records: list[dict]) -> int:
    if not records: return 0
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = False
    cur  = conn.cursor()
    rows = [
        (r["isin"], r["piotroski_score"], r["piotroski_label"],
         r["graham_criteria_met"], r["graham_pass"], r["value_quality_score"],
         r.get("trailing_pe"), r.get("payout_ratio_pct"), r.get("debt_to_equity"),
         r.get("roce_pct"), r.get("roe_pct"), r.get("dividend_yield_pct"),
         r.get("roa_cur"), r.get("current_ratio"),
         r["f1"], r["f2"], r["f3"], r["f4"], r["f5"],
         r["f6"], r["f7"], r["f8"], r["f9"])
        for r in records
    ]
    execute_values(cur, """
        INSERT INTO investmitra.value_quality
            (isin, piotroski_score, piotroski_label, graham_criteria_met, graham_pass,
             value_quality_score, trailing_pe, payout_ratio_pct, debt_to_equity,
             roce_pct, roe_pct, dividend_yield_pct, roa_cur, current_ratio,
             f1,f2,f3,f4,f5,f6,f7,f8,f9)
        VALUES %s
        ON CONFLICT (isin) DO UPDATE SET
            piotroski_score     = EXCLUDED.piotroski_score,
            piotroski_label     = EXCLUDED.piotroski_label,
            graham_criteria_met = EXCLUDED.graham_criteria_met,
            graham_pass         = EXCLUDED.graham_pass,
            value_quality_score = EXCLUDED.value_quality_score,
            trailing_pe         = EXCLUDED.trailing_pe,
            payout_ratio_pct    = EXCLUDED.payout_ratio_pct,
            debt_to_equity      = EXCLUDED.debt_to_equity,
            roce_pct            = EXCLUDED.roce_pct,
            roe_pct             = EXCLUDED.roe_pct,
            updated_at          = NOW()
    """, rows, page_size=100)
    conn.commit(); cur.close(); conn.close()
    return len(rows)


def print_results():
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()

    cur.execute("""
        SELECT vq.isin, cm.company_name, cm.sector, cm.market_cap_category,
               vq.piotroski_score, vq.graham_criteria_met, vq.value_quality_score,
               vq.trailing_pe, vq.roce_pct, vq.debt_to_equity
        FROM investmitra.value_quality vq
        JOIN investmitra.company_master cm ON vq.isin = cm.isin
        WHERE vq.piotroski_score >= 7
        ORDER BY vq.piotroski_score DESC, vq.graham_criteria_met DESC
        LIMIT 25
    """)
    rows = cur.fetchall()

    print(f"\n{'='*95}")
    print(f"TOP PIOTROSKI STOCKS (Score 7-9)")
    print(f"{'='*95}")
    print(f"{'Company':<32} {'Sector':<18} {'Cap':<6} {'F':>2} {'G':>2} {'VQ':>6} {'P/E':>6} {'ROCE%':>7} {'D/E':>7}")
    print(f"{'─'*95}")
    for r in rows:
        print(f"{str(r[1])[:31]:<32} {str(r[2])[:17]:<18} {str(r[3])[:5]:<6} "
              f"{int(r[4] or 0):>2} {int(r[5] or 0):>2} {float(r[6] or 0):>6.1f} "
              f"{float(r[7] or 0):>6.1f} {float(r[8] or 0):>7.1f} {float(r[9] or 0):>7.1f}")

    cur.execute("SELECT COUNT(*) FROM investmitra.value_quality WHERE graham_pass=TRUE")
    g_count = cur.fetchone()[0]

    cur.execute("""
        SELECT vq.isin, cm.company_name, cm.sector,
               vq.piotroski_score, vq.value_quality_score,
               vq.trailing_pe, vq.payout_ratio_pct, vq.debt_to_equity, vq.roce_pct
        FROM investmitra.value_quality vq
        JOIN investmitra.company_master cm ON vq.isin = cm.isin
        WHERE vq.graham_pass = TRUE
        ORDER BY vq.piotroski_score DESC, vq.value_quality_score DESC
    """)
    graham_rows = cur.fetchall()

    print(f"\n{'='*95}")
    print(f"BENJAMIN GRAHAM SCREEN — {g_count} stocks pass ALL 4 criteria")
    print(f"{'='*95}")
    if graham_rows:
        print(f"{'Company':<32} {'Sector':<18} {'F':>2} {'VQ':>6} {'P/E':>6} {'Div%':>6} {'D/E':>7} {'ROCE%':>7}")
        print(f"{'─'*95}")
        for r in graham_rows:
            print(f"{str(r[1])[:31]:<32} {str(r[2])[:17]:<18} "
                  f"{int(r[3] or 0):>2} {float(r[4] or 0):>6.1f} "
                  f"{float(r[5] or 0):>6.1f} {float(r[6] or 0):>6.1f} "
                  f"{float(r[7] or 0):>7.1f} {float(r[8] or 0):>7.1f}")

    cur.close(); conn.close()


def main():
    ensure_table()
    symbols  = get_symbols()
    records  = []
    failed   = 0

    logger.info("Fetching value quality + Piotroski for %d stocks...", len(symbols))

    for i, (isin, symbol) in enumerate(symbols):
        result = fetch_and_score(isin, symbol)
        if result:
            records.append(result)
        else:
            failed += 1

        if i % 100 == 0 and i > 0:
            write_to_neon(records)
            logger.info("Progress: %d/%d — scored: %d failed: %d",
                        i, len(symbols), len(records), failed)
            records = []

        time.sleep(0.3)

    if records:
        write_to_neon(records)

    logger.info("Done. Failed: %d", failed)
    print_results()


if __name__ == "__main__":
    main()
