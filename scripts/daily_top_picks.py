"""
investMITRA — Daily Top Picks
Runs every night after scores are computed:
1. Reads top Strong Buy stocks from Neon
2. Runs TradingAgents analysis on each
3. Writes final shortlist to Neon top_picks table
4. Grafana shows top 1-2 picks automatically

Run:
  python scripts/daily_top_picks.py --date 2026-08-15 --top 5
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

IST      = timezone(timedelta(hours=5, minutes=30))
NEON_URL = os.getenv("CC_POSTGRES_URL")

# TradingAgents path
TA_PATH  = os.getenv("TRADING_AGENTS_PATH", "C:/MITRAseries/TradingAgents")


def ensure_top_picks_table():
    """Create top_picks table in Neon if not exists."""
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = True
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS investmitra.top_picks (
            id                  SERIAL PRIMARY KEY,
            pick_date           DATE NOT NULL,
            rank                INTEGER NOT NULL,
            isin                VARCHAR(12),
            company_name        VARCHAR(200),
            nse_symbol          VARCHAR(20),
            sector              VARCHAR(100),
            price               DECIMAL(15,2),
            investmitra_score   DECIMAL(6,2),
            signal              VARCHAR(20),
            momentum_score      DECIMAL(6,2),
            financial_health_score DECIMAL(6,2),
            management_quality_score DECIMAL(6,2),
            ta_decision         VARCHAR(50),
            ta_thesis           TEXT,
            ta_time_horizon     VARCHAR(50),
            both_agree          BOOLEAN DEFAULT FALSE,
            ingested_at         TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (pick_date, rank)
        )
    """)
    cur.close(); conn.close()
    logger.info("top_picks table ready")


def get_top_strong_buys(score_date: date, top_n: int = 10) -> list[dict]:
    """Get top N Strong Buy stocks from daily_scores."""
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("""
        SELECT ds.isin, ds.company_name, cm.nse_symbol, ds.sector,
               ds.price, ds.investmitra_score, ds.signal,
               ds.momentum_score, ds.financial_health_score,
               ds.management_quality_score, ds.ret_252d_pct
        FROM investmitra.daily_scores ds
        LEFT JOIN investmitra.company_master cm ON ds.isin = cm.isin
        WHERE ds.score_date = %s
          AND ds.signal IN ('Strong Buy', 'Buy')
          AND cm.nse_symbol IS NOT NULL
          AND ds.financial_health_score != 50.0
          AND ds.management_quality_score != 50.0
        ORDER BY ds.investmitra_score DESC
        LIMIT %s
    """, (score_date, top_n))
    rows = cur.fetchall()
    cur.close(); conn.close()

    return [
        {
            "isin": r[0], "company_name": r[1], "nse_symbol": r[2],
            "sector": r[3], "price": r[4], "investmitra_score": r[5],
            "signal": r[6], "momentum_score": r[7],
            "financial_health_score": r[8], "management_quality_score": r[9],
            "ret_252d_pct": r[10]
        }
        for r in rows
    ]


def run_trading_agents(symbol: str, analysis_date: str) -> dict:
    """Run TradingAgents on a single stock and return decision."""
    try:
        # Load both env files explicitly
        load_dotenv('.env.prod', override=True)
        load_dotenv(f"{TA_PATH}/.env", override=True)
        sys.path.insert(0, TA_PATH)
        neon = os.getenv("CC_POSTGRES_URL", NEON_URL)
        os.environ["INVESTMITRA_NEON_URL"] = neon

        from tradingagents.graph.trading_graph import TradingAgentsGraph
        from tradingagents.default_config import DEFAULT_CONFIG

        config = DEFAULT_CONFIG.copy()
        config["deep_think_llm"]   = "gpt-4o-mini"
        config["quick_think_llm"]  = "gpt-4o-mini"
        config["max_debate_rounds"]     = 1
        config["max_risk_discuss_rounds"] = 1
        config["online_tools"] = True

        ta = TradingAgentsGraph(debug=False, config=config)
        ticker = f"{symbol}.NS"
        logger.info("Running TradingAgents for %s on %s...", ticker, analysis_date)

        state, decision = ta.propagate(ticker, analysis_date)

        # Extract decision from state
        ta_decision = "Unknown"
        ta_thesis   = ""
        ta_horizon  = ""

        if hasattr(state, 'get'):
            final = state.get("final_trade_decision", "") or state.get("portfolio_decision", "")
            if final:
                ta_thesis = str(final)[:500]
                # Parse decision
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
        return {"decision": ta_decision, "thesis": ta_thesis, "horizon": ta_horizon}

    except Exception as e:
        logger.error("TradingAgents failed for %s: %s", symbol, e)
        return {"decision": "Error", "thesis": str(e)[:200], "horizon": ""}


def save_top_picks(picks: list[dict], score_date: date):
    """Save top picks to Neon top_picks table."""
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
         p.get("ta_decision"), p.get("ta_thesis"), p.get("ta_horizon"),
         p.get("both_agree", False))
        for i, p in enumerate(picks)
    ]

    execute_values(cur, """
        INSERT INTO investmitra.top_picks
            (pick_date, rank, isin, company_name, nse_symbol, sector, price,
             investmitra_score, signal, momentum_score, financial_health_score,
             management_quality_score, ta_decision, ta_thesis, ta_time_horizon,
             both_agree)
        VALUES %s
        ON CONFLICT (pick_date, rank) DO UPDATE SET
            ta_decision  = EXCLUDED.ta_decision,
            ta_thesis    = EXCLUDED.ta_thesis,
            both_agree   = EXCLUDED.both_agree,
            ingested_at  = NOW()
    """, rows, page_size=10)

    conn.commit(); cur.close(); conn.close()
    logger.info("Saved %d top picks to Neon", len(picks))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=date.fromisoformat,
                        default=datetime.now(IST).date())
    parser.add_argument("--top",  type=int, default=5,
                        help="Number of top stocks to analyse")
    parser.add_argument("--no-ta", action="store_true",
                        help="Skip TradingAgents, just show top picks")
    args = parser.parse_args()

    ensure_top_picks_table()

    # Get top Strong Buy stocks
    candidates = get_top_strong_buys(args.date, top_n=args.top)
    if not candidates:
        logger.warning("No Strong Buy stocks found for %s", args.date)
        return

    logger.info("Top %d candidates for %s:", len(candidates), args.date)
    for i, c in enumerate(candidates):
        logger.info("  %d. %s (%s) — Score: %.1f, Sector: %s",
                    i+1, c["company_name"], c.get("nse_symbol"),
                    c["investmitra_score"], c["sector"])

    if args.no_ta:
        save_top_picks(candidates, args.date)
        print("\nTop picks saved (no TradingAgents analysis)")
        return

    # Run TradingAgents on each candidate
    date_str = args.date.isoformat()
    for c in candidates:
        symbol = c.get("nse_symbol")
        if not symbol:
            c["ta_decision"] = "N/A"
            c["ta_thesis"]   = "No NSE symbol"
            c["both_agree"]  = False
            continue

        result = run_trading_agents(symbol, date_str)
        c["ta_decision"] = result["decision"]
        c["ta_thesis"]   = result["thesis"]
        c["ta_horizon"]  = result["horizon"]

        # Check agreement
        inv_bullish = c["signal"] in ("Strong Buy", "Buy")
        ta_bullish  = result["decision"] in ("Strong Buy", "Buy", "Hold")
        c["both_agree"] = inv_bullish and ta_bullish

    # Save all picks
    save_top_picks(candidates, args.date)

    # Print summary
    print("\n" + "="*65)
    print(f"investMITRA TOP PICKS — {args.date}")
    print("="*65)
    agreed = [c for c in candidates if c.get("both_agree")]
    for i, c in enumerate(candidates):
        agree_flag = "✅ BOTH AGREE" if c.get("both_agree") else "⚠️ DIVERGE"
        print(f"\n#{i+1} {c['company_name']} ({c.get('nse_symbol')})")
        print(f"     Sector:    {c['sector']}")
        print(f"     Score:     {c['investmitra_score']:.1f} — {c['signal']}")
        print(f"     TradingAgents: {c.get('ta_decision','N/A')}")
        print(f"     {agree_flag}")

    print(f"\n{'='*65}")
    print(f"FINAL: {len(agreed)}/{len(candidates)} stocks where both systems agree")
    if agreed:
        print("RECOMMENDED BUYS:")
        for c in agreed[:2]:
            print(f"  → {c['company_name']} ({c.get('nse_symbol')}) — {c['investmitra_score']:.1f}/100")
    print("="*65)


if __name__ == "__main__":
    main()
