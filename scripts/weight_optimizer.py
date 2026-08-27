"""
investMITRA — Weight Optimizer (Claude Opus)
Weekly deep analysis of all trades.
Updates signal weights in Neon based on what actually worked.

Run: python scripts/weight_optimizer.py
Or: Triggered by GitHub Actions every Sunday
"""
from __future__ import annotations
import logging, os, json
from datetime import datetime, date, timedelta, timezone
import psycopg2
from psycopg2.extras import Json
import requests
from dotenv import load_dotenv
load_dotenv('.env.prod')

logger   = logging.getLogger(__name__)
NEON_URL = os.getenv("CC_POSTGRES_URL")
IST      = timezone(timedelta(hours=5, minutes=30))
OPUS_MODEL = "claude-opus-4-6"


def call_opus(prompt: str, max_tokens: int = 2000) -> str:
    """Call Claude Opus for deep analysis."""
    try:
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json",
                     "x-api-key": api_key,
                     "anthropic-version": "2023-06-01"},
            json={
                "model":      OPUS_MODEL,
                "max_tokens": max_tokens,
                "messages":   [{"role": "user", "content": prompt}],
            },
            timeout=60
        )
        data = r.json()
        if data.get("content"):
            return data["content"][0].get("text", "")
        return ""
    except Exception as e:
        logger.error("Opus API failed: %s", e)
        return ""


def get_weekly_trades(weeks: int = 1) -> list[dict]:
    """Get all trades from last N weeks."""
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        cur  = conn.cursor()
        cur.execute("""
            SELECT tl.id, tl.trade_date, tl.symbol, tl.direction,
                   tl.entry_price, tl.exit_price, tl.net_pnl, tl.outcome,
                   tl.hold_minutes, tl.true_gap_pct, tl.gap_type,
                   tl.rvol, tl.sector_rs, tl.sector_chg,
                   tl.final_score, tl.market_direction,
                   tl.vix_level, tl.session,
                   tl.quality_score, tl.opp_score,
                   ti.analysis, ti.suggestions
            FROM investmitra.trade_log tl
            LEFT JOIN investmitra.trade_insights ti ON tl.id = ti.trade_id
            WHERE tl.trade_date >= CURRENT_DATE - INTERVAL '%s weeks'
            ORDER BY tl.trade_date
        """ % weeks)
        cols   = [d[0] for d in cur.description]
        trades = [dict(zip(cols, row)) for row in cur.fetchall()]
        cur.close(); conn.close()
        return trades
    except Exception as e:
        logger.error("Weekly trades: %s", e)
        return []


def get_current_weights() -> dict:
    """Get current signal weights."""
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        cur  = conn.cursor()
        cur.execute("SELECT weights FROM investmitra.signal_weights ORDER BY effective_date DESC LIMIT 1")
        row = cur.fetchone()
        cur.close(); conn.close()
        if row:
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])
    except Exception as e:
        logger.warning("Weights: %s", e)
    return {}


def compute_factor_stats(trades: list[dict]) -> dict:
    """
    Statistical analysis — which factors correlated with wins?
    No AI needed for this part.
    """
    if not trades:
        return {}

    wins   = [t for t in trades if float(t.get("net_pnl", 0)) > 0]
    losses = [t for t in trades if float(t.get("net_pnl", 0)) <= 0]

    def avg(lst, key):
        vals = [float(t.get(key, 0) or 0) for t in lst if t.get(key) is not None]
        return sum(vals) / len(vals) if vals else 0

    stats = {
        "total":         len(trades),
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "total_pnl":     round(sum(float(t.get("net_pnl", 0)) for t in trades), 2),
        "avg_win":       round(avg(wins,   "net_pnl"), 2),
        "avg_loss":      round(avg(losses, "net_pnl"), 2),

        # Factor averages for wins vs losses
        "win_avg_rvol":       round(avg(wins,   "rvol"), 2),
        "loss_avg_rvol":      round(avg(losses, "rvol"), 2),
        "win_avg_gap":        round(avg(wins,   "true_gap_pct"), 2),
        "loss_avg_gap":       round(avg(losses, "true_gap_pct"), 2),
        "win_avg_sector_rs":  round(avg(wins,   "sector_rs"), 2),
        "loss_avg_sector_rs": round(avg(losses, "sector_rs"), 2),
        "win_avg_score":      round(avg(wins,   "final_score"), 2),
        "loss_avg_score":     round(avg(losses, "final_score"), 2),

        # Session breakdown
        "session_stats": {},
        "gap_type_stats": {},
        "direction_stats": {},
    }

    # Session breakdown
    for session in ("momentum", "choppy", "afternoon"):
        s_trades = [t for t in trades if t.get("session") == session]
        s_wins   = [t for t in s_trades if float(t.get("net_pnl",0)) > 0]
        if s_trades:
            stats["session_stats"][session] = {
                "trades":   len(s_trades),
                "wins":     len(s_wins),
                "win_rate": round(len(s_wins)/len(s_trades)*100, 1),
                "pnl":      round(sum(float(t.get("net_pnl",0)) for t in s_trades), 2),
            }

    # Gap type breakdown
    for gtype in ("continuation", "continuation_strong", "fade_risk", "exhaustion"):
        g_trades = [t for t in trades if t.get("gap_type") == gtype]
        g_wins   = [t for t in g_trades if float(t.get("net_pnl",0)) > 0]
        if g_trades:
            stats["gap_type_stats"][gtype] = {
                "trades":   len(g_trades),
                "wins":     len(g_wins),
                "win_rate": round(len(g_wins)/len(g_trades)*100, 1),
            }

    # Direction breakdown
    for d in ("LONG", "SHORT"):
        d_trades = [t for t in trades if t.get("direction") == d]
        d_wins   = [t for t in d_trades if float(t.get("net_pnl",0)) > 0]
        if d_trades:
            stats["direction_stats"][d] = {
                "trades":   len(d_trades),
                "wins":     len(d_wins),
                "win_rate": round(len(d_wins)/len(d_trades)*100, 1),
                "pnl":      round(sum(float(t.get("net_pnl",0)) for t in d_trades), 2),
            }

    return stats


def run_opus_weekly_review(trades: list[dict], stats: dict, current_weights: dict) -> dict:
    """Deep weekly analysis with Claude Opus."""

    # Format trades for Opus
    trade_summary = []
    for t in trades[-30:]:  # Last 30 trades max
        trade_summary.append(
            f"{t.get('trade_date')} {t.get('direction')} {t.get('symbol')} "
            f"gap:{float(t.get('true_gap_pct',0)):+.1f}% "
            f"({t.get('gap_type','?')}) "
            f"rvol:{float(t.get('rvol',0)):.1f}x "
            f"sector_rs:{float(t.get('sector_rs',0)):.0f} "
            f"session:{t.get('session','?')} "
            f"pnl:₹{float(t.get('net_pnl',0)):.0f} "
            f"({t.get('outcome','?')})"
        )

    # Get Sonnet insights
    conn = psycopg2.connect(NEON_URL, connect_timeout=10)
    cur  = conn.cursor()
    cur.execute("""
        SELECT symbol, outcome, analysis, suggestions
        FROM investmitra.trade_insights
        WHERE trade_date >= CURRENT_DATE - INTERVAL '7 days'
        ORDER BY created_at DESC LIMIT 10
    """)
    insights = cur.fetchall()
    cur.close(); conn.close()

    insights_text = "\n".join([
        f"- {r[0]} ({r[1]}): {r[2]}" for r in insights
    ]) if insights else "No insights yet"

    prompt = f"""You are the chief quantitative analyst for investMITRA, an Indian intraday trading system.

WEEKLY PERFORMANCE SUMMARY:
  Total trades:  {stats['total']}
  Win rate:      {stats['win_rate']}%
  Total P&L:     ₹{stats['total_pnl']:,.0f}
  Avg win:       ₹{stats['avg_win']:,.0f}
  Avg loss:      ₹{stats['avg_loss']:,.0f}

FACTOR ANALYSIS (wins vs losses):
  RVOL:      wins avg {stats['win_avg_rvol']:.1f}x  vs  losses avg {stats['loss_avg_rvol']:.1f}x
  Gap:       wins avg {stats['win_avg_gap']:+.2f}%  vs  losses avg {stats['loss_avg_gap']:+.2f}%
  Sector RS: wins avg {stats['win_avg_sector_rs']:.0f}  vs  losses avg {stats['loss_avg_sector_rs']:.0f}
  Score:     wins avg {stats['win_avg_score']:.1f}  vs  losses avg {stats['loss_avg_score']:.1f}

SESSION BREAKDOWN:
{json.dumps(stats.get('session_stats', {}), indent=2)}

GAP TYPE BREAKDOWN:
{json.dumps(stats.get('gap_type_stats', {}), indent=2)}

DIRECTION BREAKDOWN:
{json.dumps(stats.get('direction_stats', {}), indent=2)}

INDIVIDUAL TRADES:
{chr(10).join(trade_summary)}

DAILY AI INSIGHTS FROM THIS WEEK:
{insights_text}

CURRENT SIGNAL WEIGHTS:
{json.dumps(current_weights, indent=2)}

Based on this week's data, provide a comprehensive strategy update.
Respond in JSON only:
{{
  "weekly_summary": "2-3 sentence summary of what worked and what didn't",
  "key_findings": [
    "finding 1",
    "finding 2"
  ],
  "weight_updates": {{
    "gap_score": 0.00-0.30,
    "rvol_score": 0.00-0.25,
    "vwap_score": 0.00-0.20,
    "orb_score": 0.00-0.20,
    "holding_score": 0.00-0.15,
    "sector_rs": 0.00-0.25,
    "breadth_score": 0.00-0.10,
    "regime_score": 0.00-0.10,
    "kl_score": 0.00-0.15,
    "sent_score": 0.00-0.10,
    "bulk_score": 0.00-0.10,
    "preopen_score": 0.00-0.05
  }},
  "threshold_updates": {{
    "gap_threshold_momentum": 0.20-0.60,
    "gap_threshold_choppy": 0.40-1.00,
    "rvol_min_continuation": 1.0-3.0,
    "skip_choppy_session": true/false,
    "skip_fade_risk": true/false,
    "min_sector_chg_long": -1.0-0.0,
    "max_sector_chg_short": 0.0-1.0
  }},
  "confidence": 0.0-1.0,
  "notes": "explanation of key changes"
}}

IMPORTANT: weights must sum to exactly 1.0

HARD CONSTRAINTS (never change these):
1. Must generate signals from ALL cap categories: MICRO, SMALL, MID, LARGE
   Do not raise gap thresholds so high that MICRO/SMALL stocks are excluded
   MICRO/SMALL threshold should always be 70% of MID/LARGE threshold
2. Minimum 2-4 signals per day expected ? if win rate is low, fix quality filters
   not by raising thresholds to zero signals
3. gap_threshold_momentum must never exceed 0.40% ? beyond that no signals fire
4. Always keep rvol_min_continuation below 2.5x ? higher kills all signals"""

    response = call_opus(prompt, max_tokens=2000)

    if not response:
        logger.error("Opus returned no response")
        return {}

    try:
        clean = response.strip()
        if "```json" in clean:
            clean = clean.split("```json")[1].split("```")[0].strip()
        elif "```" in clean:
            clean = clean.split("```")[1].split("```")[0].strip()

        result = json.loads(clean)

        # Validate weights sum to 1.0
        w = result.get("weight_updates", {})
        total = sum(v for v in w.values() if isinstance(v, (int, float)))
        if abs(total - 1.0) > 0.05:
            logger.warning("Weights don't sum to 1.0 (got %.3f) — normalizing", total)
            if total > 0:
                result["weight_updates"] = {k: round(v/total, 3) for k, v in w.items()}

        return result

    except Exception as e:
        logger.error("Parse Opus response: %s\n%s", e, response[:300])
        return {}


def save_new_weights(opus_result: dict, stats: dict):
    """
    IMPORTANT: Only save weights if confidence > 0.7 AND trades > 20.
    With low trade count, Opus suggestions go to Telegram only ? not auto-applied.
    This prevents over-fitting on small data.
    """
    if stats.get("total", 0) < 20:
        # Not enough data ? send suggestions to Telegram only
        try:
            from order_manager import notify
            suggestions = opus_result.get("key_findings", [])
            notes = opus_result.get("notes", "")
            notify(
                f"OPUS WEEKLY SUGGESTIONS (not auto-applied)
"
                f"Trades: {stats['total']} (need 20+ to auto-apply)

"
                f"Findings:
" + "
".join([f"- {f}" for f in suggestions[:5]]) +
                f"

Notes: {notes[:200]}

"
                f"Review manually and apply if agree."
            )
        except: pass
        print(f"  Only {stats['total']} trades ? suggestions sent to Telegram, NOT auto-applied")
        return

    confidence = opus_result.get("confidence", 0)
    if confidence < 0.7:
        print(f"  Low confidence ({confidence:.0%}) ? not auto-applying weights")
        return

def save_new_weights(opus_result: dict, stats: dict):
    """Save updated weights to Neon."""
    if not opus_result:
        return

    weights = {
        **opus_result.get("weight_updates", {}),
        **opus_result.get("threshold_updates", {}),
    }

    if not weights:
        return

    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        conn.autocommit = True
        cur  = conn.cursor()

        # Next Monday as effective date
        today     = date.today()
        days_ahead = 7 - today.weekday()  # Monday
        next_monday = today + timedelta(days=days_ahead)

        cur.execute("""
            INSERT INTO investmitra.signal_weights
                (effective_date, weights, updated_by, trade_count, win_rate, notes)
            VALUES (%s, %s, 'weekly_opus', %s, %s, %s)
            ON CONFLICT (effective_date) DO UPDATE SET
                weights=EXCLUDED.weights,
                updated_by=EXCLUDED.updated_by,
                trade_count=EXCLUDED.trade_count,
                win_rate=EXCLUDED.win_rate,
                notes=EXCLUDED.notes,
                created_at=NOW()
        """, (
            next_monday,
            Json(weights),
            stats.get("total", 0),
            stats.get("win_rate", 0),
            opus_result.get("notes", "")[:500],
        ))
        cur.close(); conn.close()
        logger.info("New weights saved — effective from %s", next_monday)
        print(f"\n✅ New weights saved — effective from {next_monday}")
        print(f"   Win rate basis: {stats.get('win_rate')}% ({stats.get('total')} trades)")

    except Exception as e:
        logger.error("Save weights: %s", e)


def print_weekly_report(stats: dict, opus_result: dict):
    """Print formatted weekly review."""
    print(f"\n{'='*65}")
    print(f"  INVESTMITRA WEEKLY STRATEGY REVIEW")
    print(f"  {date.today()}")
    print(f"{'='*65}")
    print(f"\n  PERFORMANCE:")
    print(f"  Trades: {stats['total']} | Win rate: {stats['win_rate']}%")
    print(f"  P&L: ₹{stats['total_pnl']:,.0f} | Avg win: ₹{stats['avg_win']:,.0f} | Avg loss: ₹{stats['avg_loss']:,.0f}")

    if stats.get("session_stats"):
        print(f"\n  SESSIONS:")
        for sess, s in stats["session_stats"].items():
            print(f"  {sess:<12}: {s['trades']} trades | {s['win_rate']}% win | ₹{s['pnl']:,.0f}")

    if stats.get("gap_type_stats"):
        print(f"\n  GAP TYPES:")
        for gtype, s in stats["gap_type_stats"].items():
            print(f"  {gtype:<20}: {s['trades']} trades | {s['win_rate']}% win")

    if opus_result:
        print(f"\n  OPUS ANALYSIS:")
        print(f"  {opus_result.get('weekly_summary', '')}")
        print(f"\n  KEY FINDINGS:")
        for f in opus_result.get("key_findings", []):
            print(f"  → {f}")
        print(f"\n  CONFIDENCE: {opus_result.get('confidence', 0):.0%}")
        print(f"  NOTES: {opus_result.get('notes', '')[:200]}")

    print(f"{'='*65}\n")


def run_weekly_review(weeks: int = 1):
    """Main weekly review function."""
    print(f"\n🔍 Running weekly strategy review ({weeks} week(s) of data)...")

    trades = get_weekly_trades(weeks)
    if not trades:
        print("  No trades found for analysis")
        return

    print(f"  Found {len(trades)} trades")

    # Statistical analysis (free)
    stats = compute_factor_stats(trades)
    print(f"  Win rate: {stats['win_rate']}% | P&L: ₹{stats['total_pnl']:,.0f}")

    if stats["total"] < 5:
        print(f"  Only {stats['total']} trades — need at least 5 for Opus review")
        print_weekly_report(stats, {})
        return

    # Opus deep analysis
    print(f"  Running Claude Opus deep analysis...")
    current_weights = get_current_weights()
    opus_result     = run_opus_weekly_review(trades, stats, current_weights)

    if opus_result:
        save_new_weights(opus_result, stats)

    print_weekly_report(stats, opus_result)

    # Notify via Telegram
    try:
        from order_manager import notify
        summary = opus_result.get("weekly_summary", "") if opus_result else ""
        notify(
            f"📊 <b>WEEKLY STRATEGY REVIEW</b>\n\n"
            f"Trades: {stats['total']} | Win rate: {stats['win_rate']}%\n"
            f"Net P&L: ₹{stats['total_pnl']:,.0f}\n\n"
            f"{summary}\n\n"
            f"New weights effective Monday ✅"
        )
    except: pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_weekly_review(weeks=1)
