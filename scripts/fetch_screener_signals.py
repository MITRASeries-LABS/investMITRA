"""
investMITRA — Screener.in Signal Fetcher (Final)
Pulls stocks from curated screens covering:
  - Early signals (volume, breakout, momentum)
  - Fundamental quality (Graham, Piotroski, Magic Formula)
  - Institutional activity (FII, DII, promoter)
  - Growth (CANSLIM, multibagger, capex)
  - Valuation (undervalued, intrinsic value)
  - All market caps (micro, small, mid, large)

Stocks in 3+ screens = highest conviction signals.
Run: python scripts/fetch_screener_signals.py
"""
from __future__ import annotations
import logging, os, time
from datetime import date, datetime, timedelta, timezone
import requests
from bs4 import BeautifulSoup
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv
load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
NEON_URL = os.getenv("CC_POSTGRES_URL")
IST      = timezone(timedelta(hours=5, minutes=30))
HEADERS  = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

SCREENS = {
    # ── EARLY SIGNALS (Volume, Breakout, Momentum) ────────────────────────
    "price_volume_action":     ("440753",  "Price Volume Action — vol 5x + positive"),
    "golden_crossover":        ("336509",  "Golden Crossover — 50 DMA > 200 DMA"),
    "near_breakout_fii":       ("803777",  "Near Breakout + FII/DII Buying"),
    "breakout_high_volume":    ("392656",  "Multi-year Breakout + High Volume"),
    "52w_high_volume_filter":  ("1476133", "52w High Breakout + Volume Filter"),
    "darvas_scan":             ("4928",    "Darvas Box — near 52w high + volume"),
    "daily_upper_circuit":     ("743583",  "Daily Upper Circuit — momentum filter"),
    "momentum_stocks":         ("139059",  "Momentum Stocks — 20% momentum"),
    "tod_fod_growth":          ("1049914", "Tod Fod Growth — 50%+ near 52w high"),
    "all_time_high":           ("317933",  "All Time High Stocks"),
    "cup_and_handle":          ("539621",  "Cup and Handle Pattern"),
    "mark_minervini":          ("331347",  "Mark Minervini Trend Template"),
    "stocks_nearing_52w_high": ("4102",    "Stocks Nearing 52-week High"),
    "rsi_oversold":            ("985942",  "RSI Oversold — RSI < 30"),
    "good_stocks_52w_low":     ("7442",    "Quality Stocks near 52w Low"),
    "below_200dma_roce":       ("334875",  "Below 200 DMA + ROCE>20% — quality dip"),
    "quality_after_correction":("2327272", "Quality Growth after 40% Correction"),

    # ── INSTITUTIONAL ACTIVITY ─────────────────────────────────────────────
    "fii_buying":              ("343087",  "FII Buying — foreign institutions"),
    "strong_fii_buying":       ("461039",  "Strong FII Buying + increasing stake"),
    "fiis_are_buying":         ("342863",  "FIIs Are Buying"),
    "dii_buying":              ("347929",  "DII Buying — domestic institutions"),
    "fii_dii_buying":          ("494272",  "FII AND DII Both Buying"),
    "fii_dii_1yr":             ("1049552", "FII+DII Combined Buying 1 Year 6%+"),
    "incremental_promoter_fii":("1049553", "Incremental Promoter+FII+DII — early sign"),
    "promoter_buying":         ("390677",  "Promoter Buying Shares"),
    "promoter_holding_increase":("326554", "Promoter Holding Increasing 3yr"),
    "mutual_funds":            ("372220",  "Mutual Funds Holdings — MF buying"),

    # ── QUARTERLY RESULTS ─────────────────────────────────────────────────
    "bull_cartel":             ("1",       "Bull Cartel — quarterly growth"),
    "quarterly_growers":       ("86",      "Quarterly Growers — Q0>Q1>Q2>Q3"),
    "highest_qtr_profit":      ("47",      "Highest YoY Quarterly Profit Growth"),
    "best_latest_quarter":     ("50359",   "Best of Latest Quarter"),
    "opm_margin_expansion":    ("606293",  "YoY & QoQ OPM Margin Expansion"),
    "formula_1":               ("85",      "Formula 1 — quarterly profit > 15%"),
    "best_quarterly_results":  ("258660",  "Best Quarterly Results — 30% growth"),
    "current_qtr_growth":      ("1049543", "Current Quarterly Growth — CANSLIM"),

    # ── FUNDAMENTAL QUALITY ───────────────────────────────────────────────
    "magic_formula":           ("59",      "Magic Formula — Joel Greenblatt"),
    "joel_greenblatt":         ("142726",  "Joel Greenblatt ROCE>20% 5yr"),
    "highest_piotroski":       ("140747",  "Highest Piotroski Score"),
    "high_quality_business":   ("579",     "High Quality Business — ROCE + margins"),
    "zerodha_checklist":       ("231433",  "Zerodha Checklist — ROE>18% 5yr"),
    "high_growth_stocks":      ("20280",   "High Growth — profit 3yr>40% ROE>40%"),
    "value_stocks":            ("184",     "Value Stocks — OPM ROCE Low D/E"),
    "all_positives":           ("174",     "All Positives — all ratios good"),
    "soic_opm":                ("319573",  "SOIC Improving OPMs — margin expansion"),
    "debt_red_roce_exp":       ("1049527", "Debt Reduction + ROCE Expansion"),
    "debt_free":               ("27897",   "Debt Free Companies — mkt cap > 500 Cr"),
    "debt_free_below_book":    ("315350",  "Debt Free Below Book Value"),
    "growth_no_dilution":      ("226712",  "Growth without Dilution 10yr"),
    "quick_cash_flow":         ("32",      "Quick Cash Flow — working capital<1mo"),
    "fcf_yield":               ("5772",    "FCF Yield — good FCF + growth"),
    "high_roe_stocks":         ("136413",  "High ROE + ROCE Stocks"),

    # ── LEGENDARY INVESTOR SCREENS ────────────────────────────────────────
    "peter_lynch":             ("8117",    "Peter Lynch — fast growers low debt"),
    "canslim":                 ("44995",   "CANSLIM — O'Neil growth system"),
    "basant_maheshwari":       ("21687",   "Basant Maheshwari — ROE>30% + dividend"),
    "vijay_kedia":             ("583084",  "Vijay Kedia Filter"),
    "consistent_compounders":  ("469372",  "Consistent Compounders — Saurabh Mukherjea"),
    "marcellus_little_champs": ("218753",  "Marcellus Little Champs"),
    "safal_niveshak":          ("705",     "Safal Niveshak 10yr Quality Screen"),
    "dr_vijay_malik":          ("127771",  "Dr Vijay Malik Quality Filter"),
    "garp_stocks":             ("1706539", "GARP — Growth at Reasonable Price"),
    "value_investing_graham":  ("1174",    "Value Investing — Ben Graham Rules"),
    "ben_graham_intrinsic":    ("21404",   "Ben Graham Intrinsic Value Way"),
    "potential_100_baggers":   ("1456374", "Potential 100 Baggers — Shankar Nath"),
    "springpad_alpha":         ("2126909", "SpringPad Alpha+ — consistent growth"),

    # ── VALUATION / UNDERVALUED ───────────────────────────────────────────
    "low_10yr_earnings":       ("6994",    "Low on 10yr Avg Earnings — Graham"),
    "low_pe_high_eps":         ("120",     "Low PE + High EPS Growth"),
    "undervalued_hist_pe":     ("214280",  "Undervalued — low vs historical PE"),
    "stocks_below_intrinsic":  ("27820",   "Stocks Below Intrinsic Value"),
    "cash_exceed_mktcap":      ("66",      "Cash Exceed Market Cap — deep value"),
    "book_value_5x":           ("276307",  "Book Value > 5x Price"),
    "loss_to_profit":          ("49",      "Loss to Profit — turnaround"),
    "highest_dividend_yield":  ("3",       "Highest Dividend Yield"),
    "peg_ratio":               ("125495",  "PEG Ratio < 1"),
    "growth_future":           ("163363",  "Growth Future — PEG<0.5 D/E<0.3"),
    "blue_chips":              ("234",     "Bluest of Blue Chips — large cap"),
    "fundamentally_undervalued":("319814", "Fundamentally Strong + Undervalued"),

    # ── GROWTH & MULTIBAGGER ──────────────────────────────────────────────
    "multibagger_stocks":      ("60880",   "Multibagger Stocks — huge potential"),
    "multi_bagger_ideas":      ("280142",  "Multi-Bagger Ideas — 15% + ROCE>20%"),
    "future_multibagger_10yr": ("359174",  "Future Multi Bagger 10 Year"),
    "highest_1yr_return":      ("355766",  "Highest Return in 1 Year"),
    "highest_5yr_return":      ("334630",  "Highest Returns in 5 Years"),
    "growth_stocks":           ("178",     "Growth Stocks — G-Factor quality"),
    "best_long_term":          ("92669",   "Best Long Term Stocks"),
    "semiconductor_stocks":    ("2329204", "Semiconductor Stocks — sector theme"),
    "best_small_cap_longterm": ("954716",  "Best Small Cap for Long Term"),

    # ── CAPEX & EXPANSION ─────────────────────────────────────────────────
    "capacity_expansion":      ("97687",   "Capacity Expansion — major capex"),
    "50pct_gross_block":       ("1049540", "50% Growth in Gross Block"),
    "order_book_gt_mktcap":    ("1292263", "Order Book > Market Cap"),
    "debt_reduction":          ("126864",  "Debt Reduction — improving balance"),

    # ── MICRO CAP / PENNY (with quality filter) ───────────────────────────
    "swing_trading_growth":    ("195637",  "Swing Trading Growth Stocks"),
    "manas_vcp":               ("139223",  "Manas Sub 600cr VCP Developing"),
    "dr_finance_smallcap":     ("2151461", "Dr Finance Quality Small Cap"),
}


def fetch_screen(screen_id: str, screen_name: str) -> list[dict]:
    url = f"https://www.screener.in/screens/{screen_id}/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200: return []

        soup  = BeautifulSoup(r.text, 'html.parser')
        table = soup.find('table')
        if not table: return []

        rows   = table.find_all('tr')
        stocks = []
        for row in rows[1:]:
            cells = row.find_all(['td', 'th'])
            if not cells or len(cells) < 3: continue

            def gc(i):
                try: return cells[i].get_text(strip=True) if i < len(cells) else ''
                except: return ''

            def pn(s):
                try: return float(s.replace(',', '').strip())
                except: return None

            name = gc(1)
            if not name or name in ('Name', 'S.No.'): continue

            stocks.append({
                "company_name":  name,
                "cmp":           pn(gc(2)),
                "pe":            pn(gc(3)),
                "market_cap_cr": pn(gc(4)),
                "roce_pct":      pn(gc(10)) if len(cells) > 10 else None,
                "screen_name":   screen_name,
                "screen_id":     screen_id,
            })

        logger.info("  %-45s %d stocks", screen_name[:45], len(stocks))
        return stocks

    except Exception as e:
        logger.debug("Failed %s: %s", screen_name, e)
        return []


def get_isin_map() -> dict:
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("SELECT UPPER(company_name), isin, UPPER(nse_symbol) FROM investmitra.company_master WHERE is_active=TRUE")
    result = {}
    for name, isin, sym in cur.fetchall():
        if name: result[name] = isin
        if sym:  result[sym]  = isin
    cur.close(); conn.close()
    return result


def match_isin(name: str, isin_map: dict) -> str | None:
    upper = name.upper().strip()
    if upper in isin_map: return isin_map[upper]
    for key, isin in isin_map.items():
        if len(upper) >= 8 and upper[:8] in key: return isin
    return None


def ensure_table():
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = True
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS investmitra.screener_signals (
            id              SERIAL PRIMARY KEY,
            signal_date     DATE NOT NULL,
            screen_name     VARCHAR(100),
            screen_id       VARCHAR(20),
            isin            VARCHAR(12),
            company_name    VARCHAR(200),
            cmp             DECIMAL(15,2),
            pe              DECIMAL(10,2),
            market_cap_cr   DECIMAL(15,2),
            roce_pct        DECIMAL(8,2),
            ingested_at     TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (signal_date, screen_name, company_name)
        )
    """)
    cur.close(); conn.close()


def save_signals(stocks: list, signal_date: date, isin_map: dict):
    if not stocks: return
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = False
    cur  = conn.cursor()
    rows = [(signal_date, s["screen_name"], s["screen_id"],
             match_isin(s["company_name"], isin_map), s["company_name"],
             s.get("cmp"), s.get("pe"), s.get("market_cap_cr"), s.get("roce_pct"))
            for s in stocks]
    execute_values(cur, """
        INSERT INTO investmitra.screener_signals
            (signal_date, screen_name, screen_id, isin, company_name,
             cmp, pe, market_cap_cr, roce_pct)
        VALUES %s ON CONFLICT (signal_date, screen_name, company_name) DO NOTHING
    """, rows, page_size=200)
    conn.commit(); cur.close(); conn.close()


def print_summary(signal_date: date):
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()

    # Screen stats
    cur.execute("""
        SELECT screen_name, COUNT(*) as n, COUNT(isin) as matched
        FROM investmitra.screener_signals WHERE signal_date=%s
        GROUP BY screen_name ORDER BY n DESC
    """, (signal_date,))
    rows = cur.fetchall()
    total_signals = sum(r[1] for r in rows)

    print(f"\n{'='*70}")
    print(f"investMITRA SCREENER SIGNALS — {signal_date}")
    print(f"Screens: {len(rows)} | Total signals: {total_signals}")
    print(f"{'='*70}")

    # High conviction — 4+ screens
    for min_screens, label in [(5, "🏆 VERY HIGH CONVICTION (5+ screens)"),
                                (4, "⭐ HIGH CONVICTION (4 screens)"),
                                (3, "✅ CONVICTION (3 screens)")]:
        cur.execute("""
            SELECT company_name, isin,
                   COUNT(DISTINCT screen_name) as n,
                   STRING_AGG(screen_name, ' | ' ORDER BY screen_name) as screens,
                   MAX(cmp) as cmp, MAX(market_cap_cr) as mktcap
            FROM investmitra.screener_signals
            WHERE signal_date=%s AND isin IS NOT NULL
            GROUP BY company_name, isin
            HAVING COUNT(DISTINCT screen_name) = %s
            ORDER BY n DESC, mktcap DESC NULLS LAST
            LIMIT 20
        """, (signal_date, min_screens))
        stocks = cur.fetchall()
        if stocks:
            print(f"\n{label} — {len(stocks)} stocks")
            print(f"{'─'*70}")
            for s in stocks:
                cmp    = f"₹{float(s[4]):.0f}" if s[4] else "N/A"
                mktcap = f"₹{float(s[5]):.0f}Cr" if s[5] else "N/A"
                print(f"  {str(s[0]):<35} {cmp:>8} {mktcap:>10}")
                # Show screens on next line
                screen_list = str(s[3]).split(' | ')[:4]
                print(f"    → {' | '.join(screen_list)}")

    # Also matched ISINs in investMITRA scoring
    cur.execute("""
        SELECT ss.company_name, ss.isin, COUNT(DISTINCT ss.screen_name) as n,
               ds.investmitra_score, ds.signal
        FROM investmitra.screener_signals ss
        JOIN investmitra.daily_scores ds ON ss.isin = ds.isin
            AND ds.score_date = (SELECT MAX(score_date) FROM investmitra.daily_scores)
        WHERE ss.signal_date = %s
        GROUP BY ss.company_name, ss.isin, ds.investmitra_score, ds.signal
        HAVING COUNT(DISTINCT ss.screen_name) >= 3
        ORDER BY COUNT(DISTINCT ss.screen_name) DESC, ds.investmitra_score DESC
        LIMIT 15
    """, (signal_date,))
    combined = cur.fetchall()

    if combined:
        print(f"\n{'='*70}")
        print(f"🎯 COMBINED — Screener signals + investMITRA score")
        print(f"{'='*70}")
        print(f"{'Company':<35} {'Screens':>7} {'investMITRA':>12} {'Signal'}")
        print(f"{'─'*70}")
        for s in combined:
            print(f"  {str(s[0]):<35} {int(s[2]):>7} {float(s[3] or 0):>12.1f} {str(s[4])}")

    cur.close(); conn.close()


def main():
    ensure_table()
    today    = datetime.now(IST).date()
    isin_map = get_isin_map()
    logger.info("Fetching %d screens for %s...", len(SCREENS), today)

    for key, (screen_id, desc) in SCREENS.items():
        stocks = fetch_screen(screen_id, key)
        if stocks:
            save_signals(stocks, today, isin_map)
        time.sleep(1.5)

    print_summary(today)


if __name__ == "__main__":
    main()
