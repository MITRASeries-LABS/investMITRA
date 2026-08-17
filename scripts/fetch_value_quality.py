"""
investMITRA — Value Quality Score (Benjamin Graham Screen)
Fetches 5-year annual financial data and computes:

  1. Avg P/E (5yr)     < 15  → not overvalued
  2. Avg Dividend Payout (3yr) > 20% → shareholder friendly
  3. Debt/Equity        < 0.2 → very low debt
  4. Avg ROCE (5yr)    > 20% → consistently high returns

Scores each criterion 0-100 and combines into Value Quality Score.

Run: python scripts/fetch_value_quality.py
"""
from __future__ import annotations
import io, logging, os, time
from datetime import date, datetime, timedelta, timezone
import boto3, pandas as pd, numpy as np
import psycopg2
from psycopg2.extras import execute_values
import pyarrow as pa
import pyarrow.parquet as pq
import yfinance as yf
from dotenv import load_dotenv
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


def safe_float(v):
    try:
        f = float(v)
        return None if (np.isnan(f) or np.isinf(f)) else f
    except: return None


def fetch_value_data(isin: str, symbol: str) -> dict | None:
    """Fetch annual financials and compute value quality metrics."""
    try:
        t = yf.Ticker(f"{symbol}.NS")

        # Annual financials
        fin = t.financials          # P&L
        bs  = t.balance_sheet       # Balance sheet
        info = t.info or {}

        if fin.empty and bs.empty:
            return None

        result = {"isin": isin, "symbol": symbol}

        # ── Revenue & Net Income (5 years) ─────────────────────────────
        if not fin.empty:
            revenues = [safe_float(v) for v in fin.loc["Total Revenue"].values
                       if "Total Revenue" in fin.index][:5]
            net_incomes = [safe_float(v) for v in fin.loc["Net Income"].values
                          if "Net Income" in fin.index][:5]
            ebit_vals = [safe_float(v) for v in fin.loc["EBIT"].values
                        if "EBIT" in fin.index][:5]

            result["revenues"]    = [r/10000000 for r in revenues if r] or None  # crore
            result["net_incomes"] = [n/10000000 for n in net_incomes if n] or None
            result["ebit_vals"]   = [e/10000000 for e in ebit_vals if e] or None

        # ── Balance Sheet ───────────────────────────────────────────────
        if not bs.empty:
            debt_vals   = [safe_float(v) for v in bs.loc["Total Debt"].values
                          if "Total Debt" in bs.index][:5]
            equity_vals = [safe_float(v) for v in bs.loc["Common Stock Equity"].values
                          if "Common Stock Equity" in bs.index][:5]
            asset_vals  = [safe_float(v) for v in bs.loc["Total Assets"].values
                          if "Total Assets" in bs.index][:5]

            result["debt_vals"]   = [d/10000000 for d in debt_vals if d] or None
            result["equity_vals"] = [e/10000000 for e in equity_vals if e] or None
            result["asset_vals"]  = [a/10000000 for a in asset_vals if a] or None

        # ── Key Ratios from info ────────────────────────────────────────
        result["trailing_pe"]    = safe_float(info.get("trailingPE"))
        result["forward_pe"]     = safe_float(info.get("forwardPE"))
        result["payout_ratio"]   = safe_float(info.get("payoutRatio"))
        result["dividend_yield"] = safe_float(info.get("dividendYield"))
        result["debt_to_equity"] = safe_float(info.get("debtToEquity"))
        result["roe"]            = safe_float(info.get("returnOnEquity"))
        result["roa"]            = safe_float(info.get("returnOnAssets"))
        result["current_ratio"]  = safe_float(info.get("currentRatio"))
        result["market_cap"]     = safe_float(info.get("marketCap"))

        # ── Dividends (3-year avg payout) ──────────────────────────────
        try:
            divs = t.dividends
            if divs is not None and len(divs) > 0:
                recent_divs = divs[divs.index >= pd.Timestamp('2023-01-01', tz='Asia/Kolkata')]
                result["div_3yr_total"] = safe_float(recent_divs.sum())
                result["div_count_3yr"] = len(recent_divs)
        except: pass

        # ── Compute ROCE (Return on Capital Employed) ───────────────────
        # ROCE = EBIT / (Total Assets - Current Liabilities)
        # Proxy: EBIT / Total Assets (simpler, works with available data)
        if result.get("ebit_vals") and result.get("asset_vals"):
            min_len = min(len(result["ebit_vals"]), len(result["asset_vals"]))
            roce_vals = []
            for i in range(min_len):
                e = result["ebit_vals"][i]
                a = result["asset_vals"][i]
                if e and a and a > 0:
                    roce_vals.append(e / a * 100)
            result["roce_vals"]  = roce_vals
            result["roce_5yr_avg"] = np.mean(roce_vals) if roce_vals else None

        # ── Compute Avg P/E (5yr) ───────────────────────────────────────
        # Use trailing P/E as proxy (yfinance doesn't give historical P/E easily)
        result["pe_5yr_avg"] = result["trailing_pe"]  # best available

        return result if any(v is not None for k, v in result.items()
                            if k not in ["isin","symbol"]) else None

    except Exception as e:
        logger.debug("Failed %s: %s", symbol, e)
        return None


def compute_value_score(data: dict) -> dict:
    """Compute Value Quality Score from fetched data."""
    scores = {}

    # ── Criterion 1: P/E < 15 ──────────────────────────────────────────
    pe = data.get("pe_5yr_avg")
    if pe and pe > 0:
        if pe < 10:   scores["pe_score"] = 100
        elif pe < 15: scores["pe_score"] = 80
        elif pe < 20: scores["pe_score"] = 60
        elif pe < 30: scores["pe_score"] = 30
        else:          scores["pe_score"] = 0
    else:
        scores["pe_score"] = None

    # ── Criterion 2: Dividend Payout > 20% ────────────────────────────
    payout = data.get("payout_ratio")
    if payout and payout > 0:
        payout_pct = payout * 100
        if payout_pct > 40:   scores["dividend_score"] = 100
        elif payout_pct > 20: scores["dividend_score"] = 80
        elif payout_pct > 10: scores["dividend_score"] = 50
        else:                   scores["dividend_score"] = 20
    else:
        scores["dividend_score"] = None

    # ── Criterion 3: Debt/Equity < 0.2 ────────────────────────────────
    de = data.get("debt_to_equity")
    if de is not None:
        de_ratio = de / 100 if de > 10 else de  # normalize if in percent
        if de_ratio < 0.1:   scores["debt_score"] = 100
        elif de_ratio < 0.2: scores["debt_score"] = 80
        elif de_ratio < 0.5: scores["debt_score"] = 50
        elif de_ratio < 1.0: scores["debt_score"] = 20
        else:                  scores["debt_score"] = 0
    else:
        scores["debt_score"] = None

    # ── Criterion 4: ROCE > 20% ────────────────────────────────────────
    roce = data.get("roce_5yr_avg")
    if roce:
        if roce > 30:   scores["roce_score"] = 100
        elif roce > 20: scores["roce_score"] = 80
        elif roce > 15: scores["roce_score"] = 60
        elif roce > 10: scores["roce_score"] = 30
        else:            scores["roce_score"] = 10
    else:
        # Fallback to ROE
        roe = data.get("roe")
        if roe:
            roe_pct = roe * 100
            if roe_pct > 20:   scores["roce_score"] = 70
            elif roe_pct > 15: scores["roce_score"] = 50
            else:               scores["roce_score"] = 20
        else:
            scores["roce_score"] = None

    # ── Composite Value Quality Score ──────────────────────────────────
    available = {k: v for k, v in scores.items() if v is not None}
    if len(available) >= 2:
        weights = {"pe_score": 0.30, "dividend_score": 0.20,
                   "debt_score": 0.30, "roce_score": 0.20}
        total_w = sum(weights[k] for k in available)
        value_score = sum(available[k] * weights[k] / total_w
                         for k in available)
    else:
        value_score = None

    # ── Graham Screen (pass/fail) ───────────────────────────────────────
    pe_ok  = (pe or 999) < 15 if pe else False
    div_ok = (data.get("payout_ratio") or 0) * 100 > 20
    de_ok  = (data.get("debt_to_equity") or 999) < 20  # yfinance in percent
    roce_ok= (data.get("roce_5yr_avg") or 0) > 20

    graham_pass = pe_ok and div_ok and de_ok and roce_ok
    graham_score = sum([pe_ok, div_ok, de_ok, roce_ok])  # 0-4

    return {
        "isin":              data["isin"],
        "value_quality_score": round(value_score, 2) if value_score else None,
        "graham_pass":       graham_pass,
        "graham_criteria_met": graham_score,
        "pe_score":          scores.get("pe_score"),
        "dividend_score":    scores.get("dividend_score"),
        "debt_score":        scores.get("debt_score"),
        "roce_score":        scores.get("roce_score"),
        "trailing_pe":       data.get("pe_5yr_avg"),
        "payout_ratio_pct":  round(data.get("payout_ratio", 0) * 100, 2) if data.get("payout_ratio") else None,
        "debt_to_equity":    data.get("debt_to_equity"),
        "roce_5yr_avg":      round(data.get("roce_5yr_avg", 0), 2) if data.get("roce_5yr_avg") else None,
        "roe":               round(data.get("roe", 0) * 100, 2) if data.get("roe") else None,
        "dividend_yield":    round(data.get("dividend_yield", 0) * 100, 4) if data.get("dividend_yield") else None,
    }


def write_to_neon(records: list[dict]) -> int:
    """Save value quality scores to Neon."""
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = True
    cur  = conn.cursor()

    # Create table if not exists
    cur.execute("""
        CREATE TABLE IF NOT EXISTS investmitra.value_quality (
            isin                  VARCHAR(12) PRIMARY KEY,
            value_quality_score   DECIMAL(6,2),
            graham_pass           BOOLEAN,
            graham_criteria_met   INTEGER,
            pe_score              DECIMAL(6,2),
            dividend_score        DECIMAL(6,2),
            debt_score            DECIMAL(6,2),
            roce_score            DECIMAL(6,2),
            trailing_pe           DECIMAL(10,2),
            payout_ratio_pct      DECIMAL(8,2),
            debt_to_equity        DECIMAL(10,2),
            roce_5yr_avg          DECIMAL(8,2),
            roe                   DECIMAL(8,2),
            dividend_yield        DECIMAL(8,4),
            updated_at            TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    rows = [(r["isin"], r.get("value_quality_score"), r.get("graham_pass"),
             r.get("graham_criteria_met"), r.get("pe_score"), r.get("dividend_score"),
             r.get("debt_score"), r.get("roce_score"), r.get("trailing_pe"),
             r.get("payout_ratio_pct"), r.get("debt_to_equity"), r.get("roce_5yr_avg"),
             r.get("roe"), r.get("dividend_yield"))
            for r in records if r.get("isin")]

    execute_values(cur, """
        INSERT INTO investmitra.value_quality
            (isin, value_quality_score, graham_pass, graham_criteria_met,
             pe_score, dividend_score, debt_score, roce_score,
             trailing_pe, payout_ratio_pct, debt_to_equity, roce_5yr_avg,
             roe, dividend_yield)
        VALUES %s
        ON CONFLICT (isin) DO UPDATE SET
            value_quality_score  = EXCLUDED.value_quality_score,
            graham_pass          = EXCLUDED.graham_pass,
            graham_criteria_met  = EXCLUDED.graham_criteria_met,
            trailing_pe          = EXCLUDED.trailing_pe,
            payout_ratio_pct     = EXCLUDED.payout_ratio_pct,
            debt_to_equity       = EXCLUDED.debt_to_equity,
            roce_5yr_avg         = EXCLUDED.roce_5yr_avg,
            roe                  = EXCLUDED.roe,
            updated_at           = NOW()
    """, rows, page_size=100)

    cur.close(); conn.close()
    return len(rows)


def main():
    symbols = get_symbols()
    records = []
    failed  = 0

    logger.info("Fetching value quality data for %d stocks...", len(symbols))

    for i, (isin, symbol) in enumerate(symbols):
        data = fetch_value_data(isin, symbol)
        if data:
            score = compute_value_score(data)
            records.append(score)
        else:
            failed += 1

        if i % 100 == 0 and i > 0:
            written = write_to_neon(records)
            logger.info("Progress: %d/%d — scored: %d failed: %d",
                        i, len(symbols), len(records), failed)
            records = []

        time.sleep(0.3)

    if records:
        write_to_neon(records)

    logger.info("Done: %d failed", failed)

    # Show Graham screen results
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("""
        SELECT vq.isin, cm.company_name, cm.sector,
               vq.value_quality_score, vq.graham_criteria_met,
               vq.trailing_pe, vq.payout_ratio_pct,
               vq.debt_to_equity, vq.roce_5yr_avg
        FROM investmitra.value_quality vq
        JOIN investmitra.company_master cm ON vq.isin = cm.isin
        WHERE vq.graham_pass = TRUE
        ORDER BY vq.value_quality_score DESC
        LIMIT 20
    """)
    rows = cur.fetchall()
    cur.close(); conn.close()

    print(f"\n{'='*80}")
    print(f"BENJAMIN GRAHAM SCREEN — Stocks passing ALL 4 criteria")
    print(f"{'='*80}")
    print(f"{'Company':<35} {'Sector':<20} {'Score':>6} {'P/E':>6} {'Div%':>6} {'D/E':>6} {'ROCE%':>7}")
    print(f"{'─'*80}")
    for r in rows:
        print(f"{str(r[1]):<35} {str(r[2]):<20} {float(r[3] or 0):>6.1f} "
              f"{float(r[5] or 0):>6.1f} {float(r[6] or 0):>6.1f} "
              f"{float(r[7] or 0):>6.1f} {float(r[8] or 0):>7.1f}")
    print(f"\nTotal Graham stocks: {len(rows)}")


if __name__ == "__main__":
    main()
