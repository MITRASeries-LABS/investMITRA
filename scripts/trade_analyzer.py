"""
investMITRA — Trade Analyzer (Claude Sonnet)
Analyzes each losing trade and suggests improvements.
Called automatically after each trade closes.

Uses claude-sonnet-4-6 for speed and cost efficiency.
Opus is reserved for weekly deep review.
"""
from __future__ import annotations
import logging, os, json
from datetime import datetime, timedelta, timezone
import psycopg2
from psycopg2.extras import Json
import requests
from dotenv import load_dotenv
load_dotenv('.env.prod')

logger   = logging.getLogger(__name__)
NEON_URL = os.getenv("CC_POSTGRES_URL")
IST      = timezone(timedelta(hours=5, minutes=30))

SONNET_MODEL = "claude-sonnet-4-6"
OPUS_MODEL   = "claude-opus-4-6"


def call_claude(prompt: str, model: str = SONNET_MODEL, max_tokens: int = 1000) -> str:
    """Call Claude API and return response text."""
    try:
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json",
                     "x-api-key": api_key,
                     "anthropic-version": "2023-06-01"},
            json={
                "model":      model,
                "max_tokens": max_tokens,
                "messages":   [{"role": "user", "content": prompt}],
            },
            timeout=30
        )
        data = r.json()
        if data.get("content"):
            return data["content"][0].get("text", "")
        return ""
    except Exception as e:
        logger.error("Claude API failed: %s", e)
        return ""


def analyze_trade(trade_id: int, trade: dict) -> dict:
    """
    Analyze a single trade with Claude Sonnet.
    Returns analysis dict with issues and suggestions.
    """
    outcome   = trade.get("outcome", "")
    symbol    = trade.get("symbol", "")
    direction = trade.get("direction", "")
    net_pnl   = float(trade.get("net_pnl", 0))
    is_loss   = net_pnl < 0

    # Only analyze losses and small wins (breakeven)
    if net_pnl > 500:
        logger.info("Trade %d was profitable (₹%.0f) — skipping deep analysis", trade_id, net_pnl)
        return {}

    prompt = f"""You are an expert intraday trading analyst reviewing a trade from the investMITRA system.

TRADE DETAILS:
  Symbol:     {symbol}
  Direction:  {direction}
  Outcome:    {outcome}
  Net P&L:    ₹{net_pnl:.0f}
  Hold time:  {int(trade.get('hold_minutes') or 0)} minutes

SIGNAL CONTEXT AT ENTRY:
  True Gap:      {float(trade.get('true_gap_pct') or 0):+.2f}% (open vs prev close)
  Gap Type:      {trade.get('gap_type', 'unknown')}
  RVOL:          {float(trade.get('rvol') or 0):.1f}x (relative volume)
  Sector RS:     {float(trade.get('sector_rs') or 0):.0f}/100
  Sector Change: {float(trade.get('sector_chg') or 0):+.2f}%
  Final Score:   {float(trade.get('final_score') or 0):.1f}/100
  Quality Score: {float(trade.get('quality_score') or 0):.1f}/100
  Opp Score:     {float(trade.get('opp_score') or 0):.1f}/100

MARKET CONTEXT:
  Direction:  {trade.get('market_direction', 'NEUTRAL')}
  VIX:        {float(trade.get('vix_level') or 0):.2f}
  Session:    {trade.get('session', 'momentum')}

Analyze this trade concisely. Respond in JSON only:
{{
  "primary_issue": "one sentence explaining the main reason for loss/underperformance",
  "issues": [
    {{"factor": "factor_name", "value": "actual_value", "problem": "what was wrong"}}
  ],
  "suggestions": [
    {{"parameter": "parameter_name", "current": "current_value", "proposed": "new_value", "reason": "why"}}
  ],
  "confidence": 0.0-1.0,
  "skip_next_time_if": "condition that should prevent this trade type"
}}"""

    response = call_claude(prompt, model=SONNET_MODEL, max_tokens=800)

    if not response:
        return {}

    try:
        # Clean response
        clean = response.strip()
        if "```json" in clean:
            clean = clean.split("```json")[1].split("```")[0].strip()
        elif "```" in clean:
            clean = clean.split("```")[1].split("```")[0].strip()

        try:
            analysis = json.loads(clean)
        except Exception:
            import re
            m = re.search(r'"primary_issue": "([^"]+)"', clean)
            analysis = {
                "primary_issue": m.group(1) if m else "Analysis truncated - JSON too long",
                "issues": [],
                "suggestions": [],
                "confidence": 0.4,
                "skip_next_time_if": ""
            }

        # Save to Neon
        _save_insight(trade_id, trade, analysis, SONNET_MODEL)

        logger.info("Trade %d analyzed: %s", trade_id, analysis.get("primary_issue", ""))
        return analysis

    except Exception as e:
        logger.warning("Parse analysis failed: %s\nResponse: %s", e, response[:200])
        return {}


def _save_insight(trade_id: int, trade: dict, analysis: dict, model: str):
    """Save AI analysis to Neon."""
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        conn.autocommit = True
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO investmitra.trade_insights
                (trade_id, trade_date, symbol, outcome, ai_model,
                 analysis, issues_found, suggestions, confidence)
            VALUES (%s, CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s)
        """, (
            trade_id,
            trade.get("symbol"),
            trade.get("outcome"),
            model,
            analysis.get("primary_issue", ""),
            Json(analysis.get("issues", [])),
            Json(analysis.get("suggestions", [])),
            float(analysis.get("confidence", 0.5)),
        ))
        cur.close(); conn.close()
    except Exception as e:
        logger.warning("Save insight: %s", e)


def analyze_recent_losses(days: int = 1):
    """Analyze all unanalyzed losing trades from recent days."""
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        cur  = conn.cursor()

        # Get trades not yet analyzed
        cur.execute("""
            SELECT tl.id, tl.trade_date, tl.symbol, tl.direction,
                   tl.entry_price, tl.exit_price, tl.quantity,
                   tl.gross_pnl, tl.net_pnl, tl.outcome, tl.hold_minutes,
                   tl.true_gap_pct, tl.gap_type, tl.rvol, tl.sector_rs,
                   tl.sector_chg, tl.final_score, tl.quality_score,
                   tl.opp_score, tl.market_direction, tl.vix_level, tl.session
            FROM investmitra.trade_log tl
            LEFT JOIN investmitra.trade_insights ti ON tl.id = ti.trade_id
            WHERE tl.trade_date >= CURRENT_DATE - INTERVAL '%s days'
              AND ti.id IS NULL
              AND tl.net_pnl < 500
            ORDER BY tl.created_at DESC
        """ % days)

        cols   = [d[0] for d in cur.description]
        trades = [dict(zip(cols, row)) for row in cur.fetchall()]
        cur.close(); conn.close()

        logger.info("Analyzing %d unanalyzed trades", len(trades))

        results = []
        for trade in trades:
            trade_id = trade["id"]
            analysis = analyze_trade(trade_id, trade)
            if analysis:
                results.append({
                    "trade_id": trade_id,
                    "symbol":   trade["symbol"],
                    "outcome":  trade["outcome"],
                    "pnl":      float(trade["net_pnl"]),
                    "issue":    analysis.get("primary_issue", ""),
                })

        return results

    except Exception as e:
        logger.error("Analyze recent: %s", e)
        return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Running trade analyzer on recent losses...")
    results = analyze_recent_losses(days=7)
    print(f"\nAnalyzed {len(results)} trades:")
    for r in results:
        print(f"  {r['symbol']} {r['outcome']} ₹{r['pnl']:.0f}: {r['issue']}")
