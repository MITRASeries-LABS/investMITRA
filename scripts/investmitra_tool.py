"""
investMITRA Tool for TradingAgents
Registers get_investmitra_score() as a LangChain tool that agents can call
to fetch pre-computed investMITRA scores from Neon.

Place this file in: C:\MITRAseries\TradingAgents\tradingagents\tools\investmitra_tool.py
Then import and add to your analyst's toolkit.
"""
from __future__ import annotations
import os
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


def get_investmitra_score(ticker: str, analysis_date: Optional[str] = None) -> str:
    """
    Fetch pre-computed investMITRA scores for an Indian stock.
    
    investMITRA is a quantitative AI investment intelligence system that scores
    Indian equities on three dimensions:
    - Momentum Score (0-100): price momentum, trend strength, volume confirmation
    - Financial Health Score (0-100): debt/equity, PAT margins, revenue growth
    - Management Quality Score (0-100): promoter holding %, institutional confidence
    - Composite investMITRA Score (0-100): weighted combination (20/30/50)
    
    Backtested on Indian equities 2024-2026:
    - IC (Information Coefficient): 0.056
    - Hit Rate: 62.5% (top-ranked stocks go up in 60 days)
    - Long-Short Return: 3.55% over 60 trading days
    
    Args:
        ticker: NSE symbol (e.g. 'RELIANCE.NS', 'INFY.NS') or plain symbol ('RELIANCE')
        analysis_date: Date in YYYY-MM-DD format (default: most recent available)
    
    Returns:
        Formatted string with score details for agent consumption
    """
    try:
        import psycopg2
        
        neon_url = os.getenv("INVESTMITRA_NEON_URL") or os.getenv("CC_POSTGRES_URL")
        if not neon_url:
            return "investMITRA scores unavailable — INVESTMITRA_NEON_URL not configured"

        # Clean ticker — remove .NS/.BO suffix
        symbol = ticker.replace(".NS", "").replace(".BO", "").strip().upper()

        conn = psycopg2.connect(neon_url, connect_timeout=10)
        cur  = conn.cursor()

        # Find ISIN from NSE symbol
        cur.execute(
            "SELECT isin, company_name, sector, market_cap_category "
            "FROM investmitra.company_master "
            "WHERE UPPER(nse_symbol) = %s AND is_active = TRUE",
            (symbol,)
        )
        cm = cur.fetchone()
        if not cm:
            cur.close(); conn.close()
            return f"investMITRA: No data found for ticker '{ticker}' (symbol: {symbol})"

        isin, company_name, sector, cap_cat = cm

        # Get most recent score
        if analysis_date:
            cur.execute("""
                SELECT score_date, investmitra_score, signal,
                       momentum_score, financial_health_score, management_quality_score,
                       financial_stress_score, ret_252d_pct, debt_equity, pat_margin,
                       insider_pct, institution_pct, price
                FROM investmitra.daily_scores
                WHERE isin = %s AND score_date <= %s
                ORDER BY score_date DESC LIMIT 1
            """, (isin, analysis_date))
        else:
            cur.execute("""
                SELECT score_date, investmitra_score, signal,
                       momentum_score, financial_health_score, management_quality_score,
                       financial_stress_score, ret_252d_pct, debt_equity, pat_margin,
                       insider_pct, institution_pct, price
                FROM investmitra.daily_scores
                WHERE isin = %s
                ORDER BY score_date DESC LIMIT 1
            """, (isin,))

        row = cur.fetchone()
        cur.close(); conn.close()

        if not row:
            return (f"investMITRA: Score not yet computed for {company_name} ({symbol}). "
                    f"ISIN: {isin}, Sector: {sector}, Cap: {cap_cat}")

        (score_date, inv_score, signal, mom_score, fin_score, mgmt_score,
         stress_score, ret_252d, debt_eq, pat_margin, insider_pct,
         inst_pct, price) = row

        def fmt(v, suffix=""):
            if v is None: return "N/A"
            return f"{float(v):.1f}{suffix}"

        # Interpretation helpers
        def score_label(s):
            if s is None: return "N/A"
            s = float(s)
            if s >= 80: return f"{s:.1f} (Excellent)"
            if s >= 60: return f"{s:.1f} (Good)"
            if s >= 40: return f"{s:.1f} (Neutral)"
            if s >= 20: return f"{s:.1f} (Weak)"
            return f"{s:.1f} (Poor)"

        report = f"""
═══════════════════════════════════════════════════════
  investMITRA QUANTITATIVE SIGNAL — {company_name}
═══════════════════════════════════════════════════════
  Ticker:         {symbol} ({isin})
  Sector:         {sector or 'N/A'}
  Market Cap:     {cap_cat or 'N/A'}
  Price (last):   ₹{fmt(price)}
  Score Date:     {score_date}

  ┌─────────────────────────────────────────────────┐
  │  COMPOSITE investMITRA SCORE: {fmt(inv_score)}/100         │
  │  SIGNAL: {signal or 'N/A':<40}│
  └─────────────────────────────────────────────────┘

  Component Scores (backtested, IC=0.056, Hit Rate=62.5%):
  ─────────────────────────────────────────────────
  Momentum Score:         {score_label(mom_score)}
    → Price trend, volume, 52-week position
  
  Financial Health Score: {score_label(fin_score)}
    → Inverse of financial stress
    → Debt/Equity: {fmt(debt_eq)}x
    → PAT Margin:  {fmt(pat_margin, '%') if pat_margin else 'N/A'}
  
  Management Quality Score: {score_label(mgmt_score)}
    → Promoter Holding: {fmt(insider_pct, '%')}
    → Institutional:    {fmt(inst_pct, '%')}

  Key Metrics:
  ─────────────────────────────────────────────────
  1-Year Price Return:    {fmt(ret_252d, '%')}
  Financial Stress Score: {fmt(stress_score)}/100 (lower=healthier)

  investMITRA Interpretation:
  ─────────────────────────────────────────────────
  {_get_interpretation(inv_score, signal, mom_score, fin_score, mgmt_score)}

  Note: investMITRA scores are quantitative signals derived from
  10 years of NSE/BSE data. They are sector-relative percentile
  ranks, not absolute valuations. Use alongside fundamental and
  technical analysis for complete picture.
═══════════════════════════════════════════════════════
"""
        return report.strip()

    except Exception as e:
        logger.error("investMITRA tool error: %s", e)
        return f"investMITRA: Error fetching score for {ticker}: {e}"


def _get_interpretation(inv_score, signal, mom_score, fin_score, mgmt_score) -> str:
    """Generate plain-English interpretation for agents."""
    parts = []

    if inv_score is None:
        return "Insufficient data for interpretation."

    inv_score = float(inv_score)

    if inv_score >= 80:
        parts.append("QUANTITATIVE SYSTEM FLAGS AS STRONG BUY: All three dimensions score well above sector peers.")
    elif inv_score >= 60:
        parts.append("QUANTITATIVE SYSTEM FLAGS AS BUY: Above-average across most dimensions relative to sector.")
    elif inv_score >= 40:
        parts.append("QUANTITATIVE SYSTEM FLAGS AS NEUTRAL: Mixed signals — some dimensions strong, others weak.")
    elif inv_score >= 20:
        parts.append("QUANTITATIVE SYSTEM FLAGS AS SELL: Below-average across most dimensions relative to sector.")
    else:
        parts.append("QUANTITATIVE SYSTEM FLAGS AS STRONG SELL: Weak across all three dimensions vs sector peers.")

    if mom_score and float(mom_score) < 30:
        parts.append("Momentum is weak — price trend is poor relative to sector.")
    elif mom_score and float(mom_score) > 70:
        parts.append("Momentum is strong — price trend is outperforming sector.")

    if mgmt_score and float(mgmt_score) > 70:
        parts.append("High promoter holding and institutional confidence suggest aligned management.")
    elif mgmt_score and float(mgmt_score) < 30:
        parts.append("Low promoter holding or weak institutional coverage — governance risk flag.")

    if fin_score and float(fin_score) < 30:
        parts.append("Financial health is weak — high debt or poor margins relative to sector peers.")
    elif fin_score and float(fin_score) > 70:
        parts.append("Financial health is strong — low debt, healthy margins.")

    return " ".join(parts)


# ─── LangChain Tool Registration ─────────────────────────────────────────────

def create_investmitra_langchain_tool():
    """
    Create a LangChain tool from the investMITRA score function.
    Call this and add to your analyst's tools list.
    
    Usage in TradingAgents:
        from tradingagents.tools.investmitra_tool import create_investmitra_langchain_tool
        investmitra_tool = create_investmitra_langchain_tool()
        # Add to analyst's tools: tools=[..., investmitra_tool]
    """
    try:
        from langchain_core.tools import tool as lc_tool

        @lc_tool
        def investmitra_score(ticker: str, analysis_date: str = "") -> str:
            """
            Fetch pre-computed investMITRA quantitative scores for an Indian stock.
            investMITRA is a backtested AI system scoring Indian equities on
            Momentum (price trend), Financial Health (debt/margins), and
            Management Quality (promoter holding). Use this to get a quantitative
            signal before forming your analysis. Call with NSE ticker like 'RELIANCE.NS'.
            """
            return get_investmitra_score(ticker, analysis_date or None)

        return investmitra_tool

    except ImportError:
        logger.warning("LangChain not available — returning plain function")
        return get_investmitra_score


if __name__ == "__main__":
    # Test the tool directly
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE.NS"
    print(get_investmitra_score(ticker))
