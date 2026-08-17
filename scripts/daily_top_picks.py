"""
investMITRA — Daily Top Picks v2
Triple confirmation: investMITRA score + Screener signals + TradingAgents

Flow:
  1. Get top Strong Buy stocks from daily_scores
  2. Cross-reference with Screener signals (must appear in 2+ screens)
  3. Run TradingAgents on shortlist
  4. Final picks = all three agree

Run:
  python scripts/daily_top_picks.py --date 2026-08-18 --top 5
  python scripts/daily_top_picks.py --date 2026-08-18 --top 5 --no-ta
"""
from __future__ import annotations
import argparse, logging, os, sys
from datetime import date, datetime, timedelta, timezone
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv
load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NEON_URL = os.getenv("CC_POSTGRES_URL")
IST      = timezone(timedelta(hours=5, minutes=30))
TA_PATH  = os.getenv("TRADING_AGENTS_PATH", "C:/MITRAseries/TradingAgents")


def ensure_tables():
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = True
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS investmitra.top_picks (
            id                       SERIAL PRIMARY KEY,
            pick_date                DATE NOT NULL,
            rank                     INTEGER NOT NULL,
            isin                     VARCHAR(12),
            company_name             VARCHAR(200),
            nse_symbol               VARCHAR(20),
            sector                   VARCHAR(100),
            price                    DECIMAL(15,2),
            investmitra_score        DECIMAL(6,2),
            signal                   VARCHAR(20),
            momentum_score           DECIMAL(6,2),
            financial_health_score   DECIMAL(6,2),
            management_quality_score DECIMAL(6,2),
            screen_count             INTEGER DEFAULT 0,
            screens_list             TEXT,
            ta_decision              VARCHAR(50),
            ta_thesis                TEXT,
            ta_time_horizon          VARCHAR(50),
            both_agree               BOOLEAN DEFAULT FALSE,
            triple_confirm           BOOLEAN DEFAULT FALSE,
            ingested_at              TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (pick_date, rank)
        )
    """)
    cur.close(); conn.close()


def get_top_candidates(score_date: date, top_n: int = 10) -> list[dict]:
    """
    Get top candidates ranked by combined investMITRA score + Screener signal count.
    Requires: Strong/Buy signal + NSE symbol + all 3 score components populated.
    """
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("""
        SELECT ds.isin, ds.company_name, cm.nse_symbol, ds.sector,
               ds.price, ds.investmitra_score, ds.signal,
               ds.momentum_score, ds.financial_health_score,
               ds.management_quality_score, ds.ret_252d_pct,
               COALESCE(ss.screen_count, 0) AS screen_count,
               COALESCE(ss.screens_list, '') AS screens_list
        FROM investmitra.daily_scores ds
        LEFT JOIN investmitra.company_master cm ON ds.isin = cm.isin
        LEFT JOIN (
            SELECT isin,
                   COUNT(DISTINCT screen_name) AS screen_count,
                   STRING_AGG(DISTINCT screen_name, ', ' ORDER BY screen_name) AS screens_list
            FROM investmitra.screener_signals
            WHERE signal_date = (SELECT MAX(signal_date) FROM investmitra.screener_signals)
            GROUP BY isin
        ) ss ON ds.isin = ss.isin
        WHERE ds.score_date = %s
          AND ds.signal IN ('Strong Buy', 'Buy')
          AND cm.nse_symbol IS NOT NULL
          AND ds.financial_health_score IS NOT NULL
          AND ds.financial_health_score != 50.0
          AND ds.management_quality_score IS NOT NULL
          AND ds.management_quality_score != 50.0
        ORDER BY (ds.investmitra_score * 0.6 + COALESCE(ss.screen_count, 0) * 2.0) DESC
        LIMIT %s
    """, (score_date, top_n))

    rows = cur.fetchall()
    cur.close(); conn.close()

    return [{
        "isin":                    r[0],
        "company_name":            r[1],
        "nse_symbol":              r[2],
        "sector":                  r[3],
        "price":                   r[4],
        "investmitra_score":       r[5],
        "signal":                  r[6],
        "momentum_score":          r[7],
        "financial_health_score":  r[8],
        "management_quality_score":r[9],
        "ret_252d_pct":            r[10],
        "screen_count":            r[11],
        "screens_list":            r[12],
    } for r in rows]


def run_trading_agents(symbol: str, analysis_date: str) -> dict:
    """Run TradingAgents on a single stock."""
    try:
        load_dotenv(f"{TA_PATH}/.env", override=True)
        sys.path.insert(0, TA_PATH)
        neon = os.getenv("CC_POSTGRES_URL", NEON_URL)
        os.environ["INVESTMITRA_NEON_URL"] = neon

        from tradingagents.graph.trading_graph import TradingAgentsGraph
        from tradingagents.default_config import DEFAULT_CONFIG

        config = DEFAULT_CONFIG.copy()
        config["deep_think_llm"]          = "gpt-4o-mini"
        config["quick_think_llm"]         = "gpt-4o-mini"
        config["max_debate_rounds"]       = 1
        config["max_risk_discuss_rounds"] = 1
        config["online_tools"]            = True

        ta     = TradingAgentsGraph(debug=False, config=config)
        ticker = f"{symbol}.NS"
        logger.info("Running TradingAgents for %s on %s...", ticker, analysis_date)

        state, decision = ta.propagate(ticker, analysis_date)

        ta_decision = "Unknown"
        ta_thesis   = ""

        if hasattr(state, 'get'):
            final = state.get("final_trade_decision", "") or state.get("portfolio_decision", "")
            if final:
                ta_thesis   = str(final)[:500]
                final_upper = final.upper()
                if "STRONG BUY" in final_upper or "OVERWEIGHT" in final_upper:
                    ta_decision = "Strong Buy"
                elif "BUY" in final_upper:
                    ta_decision = "Buy"
                elif "HOLD" in final_upper or "NEUTRAL" in final_upper:
                    ta_decision = "Hold"
                elif "UNDERWEIGHT" in final_upper or "TRIM" in final_upper:
                    ta_decision = "Underweight"
                elif "SELL" in final_upper:
                    ta_decision = "Sell"

        logger.info("  %s → %s", ticker, ta_decision)
        return {"decision": ta_decision, "thesis": ta_thesis}

    except Exception as e:
        logger.error("TradingAgents failed for %s: %s", symbol, e)
        return {"decision": "Error", "thesis": str(e)[:200]}


def save_top_picks(picks: list[dict], score_date: date):
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = False
    cur  = conn.cursor()

    rows = [
        (score_date, i + 1,
         p["isin"], p["company_name"], p.get("nse_symbol"),
         p["sector"], float(p["price"] or 0),
         float(p["investmitra_score"] or 0), p["signal"],
         float(p["momentum_score"] or 0),
         float(p["financial_health_score"] or 0),
         float(p["management_quality_score"] or 0),
         int(p.get("screen_count", 0)),
         p.get("screens_list", ""),
         p.get("ta_decision"), p.get("ta_thesis"), p.get("ta_horizon"),
         p.get("both_agree", False),
         p.get("triple_confirm", False))
        for i, p in enumerate(picks)
    ]

    execute_values(cur, """
        INSERT INTO investmitra.top_picks
            (pick_date, rank, isin, company_name, nse_symbol, sector, price,
             investmitra_score, signal, momentum_score, financial_health_score,
             management_quality_score, screen_count, screens_list,
             ta_decision, ta_thesis, ta_time_horizon,
             both_agree, triple_confirm)
        VALUES %s
        ON CONFLICT (pick_date, rank) DO UPDATE SET
            ta_decision    = EXCLUDED.ta_decision,
            ta_thesis      = EXCLUDED.ta_thesis,
            both_agree     = EXCLUDED.both_agree,
            triple_confirm = EXCLUDED.triple_confirm,
            screen_count   = EXCLUDED.screen_count,
            ingested_at    = NOW()
    """, rows, page_size=10)

    conn.commit(); cur.close(); conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date",    type=date.fromisoformat, default=datetime.now(IST).date())
    parser.add_argument("--top",     type=int, default=5)
    parser.add_argument("--no-ta",   action="store_true")
    args = parser.parse_args()

    ensure_tables()

    candidates = get_top_candidates(args.date, top_n=args.top)
    if not candidates:
        logger.warning("No candidates for %s", args.date)
        return

    logger.info("Top %d candidates for %s:", len(candidates), args.date)
    for i, c in enumerate(candidates):
        logger.info("  %d. %s (%s) — Score: %.1f | Screens: %d | Sector: %s",
                    i+1, c["company_name"], c.get("nse_symbol"),
                    c["investmitra_score"], c["screen_count"], c["sector"])

    if args.no_ta:
        for c in candidates:
            c["both_agree"]    = c["investmitra_score"] >= 70 and c["screen_count"] >= 2
            c["triple_confirm"] = False
        save_top_picks(candidates, args.date)
        _print_summary(candidates, args.date)
        return

    # Run TradingAgents
    date_str = args.date.isoformat()
    for c in candidates:
        symbol = c.get("nse_symbol")
        if not symbol:
            c["ta_decision"] = "N/A"
            c["ta_thesis"]   = "No NSE symbol"
            continue

        result           = run_trading_agents(symbol, date_str)
        c["ta_decision"] = result["decision"]
        c["ta_thesis"]   = result["thesis"]
        c["ta_horizon"]  = ""

        # Both agree = investMITRA bullish + TradingAgents bullish
        inv_bullish = c["signal"] in ("Strong Buy", "Buy")
        ta_bullish  = result["decision"] in ("Strong Buy", "Buy", "Hold")
        c["both_agree"] = inv_bullish and ta_bullish

        # Triple confirm = all three: investMITRA + Screener (2+ screens) + TradingAgents
        c["triple_confirm"] = inv_bullish and ta_bullish and c["screen_count"] >= 2

    save_top_picks(candidates, args.date)
    _print_summary(candidates, args.date)


def _print_summary(picks: list[dict], score_date: date):
    print(f"\n{'='*70}")
    print(f"investMITRA TOP PICKS — {score_date}")
    print(f"{'='*70}")

    for i, c in enumerate(picks):
        triple = "🏆 TRIPLE CONFIRM" if c.get("triple_confirm") else \
                 "✅ BOTH AGREE"     if c.get("both_agree")    else "⚠️  DIVERGE"
        print(f"\n#{i+1} {c['company_name']} ({c.get('nse_symbol')})")
        print(f"     Sector:          {c['sector']}")
        print(f"     investMITRA:     {c['investmitra_score']:.1f} — {c['signal']}")
        print(f"     Screener Screens:{c.get('screen_count', 0)}")
        print(f"     TradingAgents:   {c.get('ta_decision', 'N/A')}")
        print(f"     {triple}")

    triple = [c for c in picks if c.get("triple_confirm")]
    agreed = [c for c in picks if c.get("both_agree") and not c.get("triple_confirm")]

    print(f"\n{'='*70}")
    if triple:
        print(f"🏆 TRIPLE CONFIRMED BUYS ({len(triple)}):")
        for c in triple:
            print(f"  → {c['company_name']} ({c.get('nse_symbol')}) "
                  f"— Score: {c['investmitra_score']:.1f} | Screens: {c.get('screen_count',0)}")
    if agreed:
        print(f"✅ BOTH AGREE ({len(agreed)}):")
        for c in agreed[:3]:
            print(f"  → {c['company_name']} ({c.get('nse_symbol')}) "
                  f"— Score: {c['investmitra_score']:.1f} | Screens: {c.get('screen_count',0)}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
