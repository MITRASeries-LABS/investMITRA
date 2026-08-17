"""
investMITRA — Screener.in Signal Fetcher v3
Uses full URL with slug (required by Screener.in).
88 curated screens covering all signals and market caps.

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
HEADERS  = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.screener.in"}

# (screen_id, slug, description)
SCREENS = {
    "price_volume_action":     ("440753",  "price-volume-action",         "Price Volume Action — vol 5x + positive"),
    "golden_crossover":        ("336509",  "golden-crossover",            "Golden Crossover — 50 DMA > 200 DMA"),
    "near_breakout_fii":       ("803777",  "near-breakout-stocks-in-uptrend", "Near Breakout + FII/DII Buying"),
    "breakout_high_volume":    ("392656",  "multi-year-breakout-with-high-volume", "Multi-year Breakout + High Volume"),
    "52w_high_volume_filter":  ("1476133", "52-week-high-breakout-stocks-with-volume-filter", "52w High Breakout + Volume"),
    "darvas_scan":             ("4928",    "darvas-scan",                 "Darvas Box Scan"),
    "daily_upper_circuit":     ("743583",  "daily-upper-circuit",         "Daily Upper Circuit"),
    "momentum_stocks":         ("139059",  "momentum-stocks",             "Momentum Stocks 20%"),
    "tod_fod_growth":          ("1049914", "tod-fod-growth",              "Tod Fod Growth 50%+"),
    "all_time_high":           ("317933",  "all-time-high-stocks",        "All Time High Stocks"),
    "cup_and_handle":          ("539621",  "cup-and-handle",              "Cup and Handle Pattern"),
    "mark_minervini":          ("331347",  "mark-minervini-trend-template","Mark Minervini Trend Template"),
    "stocks_nearing_52w_high": ("4102",    "stocks-nearing-52-week-high", "Stocks Nearing 52-week High"),
    "rsi_oversold":            ("985942",  "rsi-oversold-stocks",         "RSI Oversold RSI<30"),
    "good_stocks_52w_low":     ("7442",    "good-stocks-near-52-week-low","Quality Stocks near 52w Low"),
    "below_200dma_roce":       ("334875",  "stocks-below-200-dma-and-roce-20-and-mc-500", "Below 200DMA + ROCE>20%"),
    "quality_after_correction":("2327272", "quality-growth-stocks-after-40-correction", "Quality after 40% Correction"),
    "fii_buying":              ("343087",  "fii-buying",                  "FII Buying"),
    "strong_fii_buying":       ("461039",  "strong-fii-buying",           "Strong FII Buying"),
    "fiis_are_buying":         ("342863",  "fiis-are-buying",             "FIIs Are Buying"),
    "dii_buying":              ("347929",  "dii-buying",                  "DII Buying"),
    "fii_dii_buying":          ("494272",  "fii-and-dii-increase-stake",  "FII AND DII Both Buying"),
    "fii_dii_1yr":             ("1049552", "fiidii-lapping-up-in-1-year", "FII+DII 1 Year 6%+"),
    "incremental_promoter_fii":("1049553", "incremental-change-in-promoter-and-fiidii-holging", "Incremental Promoter+FII+DII"),
    "promoter_buying":         ("390677",  "promoter-buying-shares",      "Promoter Buying Shares"),
    "promoter_holding_increase":("326554", "promoter-holding-increase",   "Promoter Holding Increasing"),
    "mutual_funds":            ("372220",  "mutual-funds",                "Mutual Funds Holdings"),
    "bull_cartel":             ("1",       "the-bull-cartel",             "Bull Cartel Quarterly Growth"),
    "quarterly_growers":       ("86",      "quarterly-growers",           "Quarterly Growers Q0>Q1>Q2>Q3"),
    "highest_qtr_profit":      ("47",      "highest-yoy-quarterly-profit-growth", "Highest YoY Quarterly Profit"),
    "best_latest_quarter":     ("50359",   "best-of-latest-quarter",      "Best of Latest Quarter"),
    "opm_margin_expansion":    ("606293",  "yoy-qoq-opm-margin-expansion-recent-qtr-tracker", "OPM Margin Expansion"),
    "formula_1":               ("85",      "formula-1",                   "Formula 1 Quarterly>15%"),
    "best_quarterly_results":  ("258660",  "best-quarterly-results",      "Best Quarterly Results"),
    "current_qtr_growth":      ("1049543", "current-quarterly-growth-rates","Current Quarterly Growth"),
    "magic_formula":           ("59",      "magic-formula",               "Magic Formula Greenblatt"),
    "joel_greenblatt":         ("142726",  "joel-greenblatt-magic-formula","Joel Greenblatt ROCE>20%"),
    "highest_piotroski":       ("140747",  "highest-piotroski-score",     "Highest Piotroski Score"),
    "high_quality_business":   ("579",     "high-quality-businesses",     "High Quality Business"),
    "zerodha_checklist":       ("231433",  "zerodha-checklist",           "Zerodha Checklist ROE>18%"),
    "high_growth_stocks":      ("20280",   "high-growth-stocks",          "High Growth 3yr>40%"),
    "value_stocks":            ("184",     "value-stocks",                "Value Stocks OPM ROCE"),
    "all_positives":           ("174",     "all-positives",               "All Positives"),
    "soic_opm":                ("319573",  "soic",                        "SOIC Improving OPMs"),
    "debt_red_roce_exp":       ("1049527", "debt-reduction-roce-expansion","Debt Reduction+ROCE Expansion"),
    "debt_free":               ("27897",   "debt-free-companies",         "Debt Free mkt>500Cr"),
    "debt_free_below_book":    ("315350",  "debt-free-with-below-book-value","Debt Free Below Book"),
    "growth_no_dilution":      ("226712",  "growth-without-dilution",     "Growth No Dilution 10yr"),
    "quick_cash_flow":         ("32",      "quick-cash-flow",             "Quick Cash Flow"),
    "fcf_yield":               ("5772",    "fcf-yield",                   "FCF Yield"),
    "high_roe_stocks":         ("136413",  "high-roe-stocks",             "High ROE ROCE Stocks"),
    "peter_lynch":             ("8117",    "peter-lynch-stock-screener",  "Peter Lynch Fast Growers"),
    "canslim":                 ("44995",   "canslim-stocks",              "CANSLIM O'Neil"),
    "basant_maheshwari":       ("21687",   "basant-maheshwari-screen",    "Basant Maheshwari ROE>30%"),
    "vijay_kedia":             ("583084",  "vijay-kedia-actual-filter",   "Vijay Kedia Filter"),
    "consistent_compounders":  ("469372",  "consistent-compounders-saurabh-mukherjea","Consistent Compounders"),
    "marcellus_little_champs": ("218753",  "marcellus-little-champs",     "Marcellus Little Champs"),
    "safal_niveshak":          ("705",     "safal_niveshak_10yrs",        "Safal Niveshak 10yr"),
    "dr_vijay_malik":          ("127771",  "dr-vijay-malik",              "Dr Vijay Malik Quality"),
    "garp_stocks":             ("1706539", "garp-stocks",                 "GARP Growth Reasonable Price"),
    "value_investing_graham":  ("1174",    "value-investing-stocks",      "Value Investing Ben Graham"),
    "ben_graham_intrinsic":    ("21404",   "the-ben-graham-way-intrinsic-value-stocks","Ben Graham Intrinsic Value"),
    "potential_100_baggers":   ("1456374", "potential-100-baggers-by-shankar-nath","Potential 100 Baggers"),
    "springpad_alpha":         ("2126909", "springpads-alpha-investing-screener","SpringPad Alpha+"),
    "low_10yr_earnings":       ("6994",    "low-on-10-year-average-earnings","Low 10yr Avg Earnings"),
    "low_pe_high_eps":         ("120",     "low-pe-and-high-eps-growth",  "Low PE High EPS Growth"),
    "undervalued_hist_pe":     ("214280",  "undervalued-companies",       "Undervalued vs Historical PE"),
    "stocks_below_intrinsic":  ("27820",   "stocks-below-intrinsic-value","Stocks Below Intrinsic Value"),
    "cash_exceed_mktcap":      ("66",      "cash-exceed-marketcap",       "Cash Exceed Market Cap"),
    "book_value_5x":           ("276307",  "book-value-over-5-times-price","Book Value > 5x Price"),
    "loss_to_profit":          ("49",      "loss-to-profit-companies",    "Loss to Profit Turnaround"),
    "highest_dividend_yield":  ("3",       "highest-dividend-yield-shares","Highest Dividend Yield"),
    "peg_ratio":               ("125495",  "peg-ratio",                   "PEG Ratio < 1"),
    "growth_future":           ("163363",  "growth-stocks-for-future",    "Growth Future PEG<0.5"),
    "blue_chips":              ("234",     "bluest-of-the-blue-chips",    "Bluest of Blue Chips"),
    "fundamentally_undervalued":("319814", "fundamentally-strong-undervalued-stocks","Fundamentally Strong Undervalued"),
    "multibagger_stocks":      ("60880",   "multibagger-stocks",          "Multibagger Stocks"),
    "multi_bagger_ideas":      ("280142",  "multi-bagger-ideas",          "Multi-Bagger Ideas"),
    "future_multibagger_10yr": ("359174",  "future-multi-bagger-stocks-in-10-year","Future Multi Bagger 10yr"),
    "highest_1yr_return":      ("355766",  "highest-return-in-1-year",    "Highest Return 1 Year"),
    "highest_5yr_return":      ("334630",  "highest-returns-in-5-year",   "Highest Returns 5 Years"),
    "growth_stocks":           ("178",     "growth-stocks",               "Growth Stocks G-Factor"),
    "best_long_term":          ("92669",   "best-long-term-stocks",       "Best Long Term Stocks"),
    "semiconductor_stocks":    ("2329204", "semiconductor-stocks",        "Semiconductor Stocks"),
    "best_small_cap_longterm": ("954716",  "best-small-cap-stocks-for-long-term","Best Small Cap Long Term"),
    "capacity_expansion":      ("97687",   "capacity-expansion",          "Capacity Expansion Capex"),
    "50pct_gross_block":       ("1049540", "50-growth-in-gross-block",    "50% Growth Gross Block"),
    "order_book_gt_mktcap":    ("1292263", "order-book-greater-than-market-cap","Order Book > Market Cap"),
    "debt_reduction":          ("126864",  "debt-reduction",              "Debt Reduction"),
    "swing_trading_growth":    ("195637",  "swing-trading-growth-stocks", "Swing Trading Growth"),
    "manas_vcp":               ("139223",  "manas-sub-600-crore-ff",      "Manas Sub 600cr VCP"),
    "dr_finance_smallcap":     ("2151461", "dr-finance-quality-alpha-investing-strategy","Dr Finance Quality SmallCap"),
}


def fetch_screen(screen_id: str, slug: str, screen_name: str) -> list[dict]:
    url = f"https://www.screener.in/screens/{screen_id}/{slug}/"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            logger.debug("HTTP %d for %s", r.status_code, screen_name)
            return []

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

    cur.execute("SELECT COUNT(DISTINCT screen_name), COUNT(*), COUNT(isin) FROM investmitra.screener_signals WHERE signal_date=%s", (signal_date,))
    r = cur.fetchone()
    print(f"\n{'='*70}")
    print(f"investMITRA SCREENER SIGNALS — {signal_date}")
    print(f"Screens: {r[0]} | Signals: {r[1]} | Matched ISINs: {r[2]}")
    print(f"{'='*70}")

    for min_s, label in [(5,"🏆 VERY HIGH (5+ screens)"),(4,"⭐ HIGH (4 screens)"),(3,"✅ CONVICTION (3 screens)")]:
        cur.execute("""
            SELECT company_name, isin, COUNT(DISTINCT screen_name) as n,
                   STRING_AGG(screen_name, ' | ' ORDER BY screen_name) as screens,
                   MAX(cmp) as cmp, MAX(market_cap_cr) as mc
            FROM investmitra.screener_signals
            WHERE signal_date=%s AND isin IS NOT NULL
            GROUP BY company_name, isin
            HAVING COUNT(DISTINCT screen_name) = %s
            ORDER BY n DESC, mc DESC NULLS LAST LIMIT 15
        """, (signal_date, min_s))
        stocks = cur.fetchall()
        if stocks:
            print(f"\n{label} — {len(stocks)} stocks")
            print(f"{'─'*70}")
            for s in stocks:
                cmp = f"₹{float(s[4]):.0f}" if s[4] else "N/A"
                mc  = f"₹{float(s[5]):.0f}Cr" if s[5] else "?"
                print(f"  {str(s[0]):<35} {cmp:>8} {mc:>10}")
                print(f"    → {' | '.join(str(s[3]).split(' | ')[:3])}")

    # Combined with investMITRA
    cur.execute("""
        SELECT ss.company_name, ss.isin, COUNT(DISTINCT ss.screen_name) as n,
               ds.investmitra_score, ds.signal
        FROM investmitra.screener_signals ss
        JOIN investmitra.daily_scores ds ON ss.isin=ds.isin
            AND ds.score_date=(SELECT MAX(score_date) FROM investmitra.daily_scores)
        WHERE ss.signal_date=%s
        GROUP BY ss.company_name, ss.isin, ds.investmitra_score, ds.signal
        HAVING COUNT(DISTINCT ss.screen_name) >= 2
        ORDER BY COUNT(DISTINCT ss.screen_name) DESC, ds.investmitra_score DESC
        LIMIT 20
    """, (signal_date,))
    combined = cur.fetchall()

    if combined:
        print(f"\n{'='*70}")
        print(f"🎯 COMBINED — Screener + investMITRA score")
        print(f"{'='*70}")
        print(f"{'Company':<35} {'Screens':>7} {'Score':>8} {'Signal'}")
        print(f"{'─'*70}")
        for s in combined:
            print(f"  {str(s[0]):<35} {int(s[2]):>7} {float(s[3] or 0):>8.1f} {str(s[4])}")

    cur.close(); conn.close()


def main():
    ensure_table()
    today    = datetime.now(IST).date()
    isin_map = get_isin_map()
    logger.info("Fetching %d screens for %s...", len(SCREENS), today)

    for key, (sid, slug, desc) in SCREENS.items():
        stocks = fetch_screen(sid, slug, key)
        if stocks:
            save_signals(stocks, today, isin_map)
        time.sleep(1.5)

    print_summary(today)


if __name__ == "__main__":
    main()
