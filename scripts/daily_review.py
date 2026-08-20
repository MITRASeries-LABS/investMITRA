"""
investMITRA — Daily Review
Runs every evening after market close.
Analyzes today's trades with Sonnet and sends Telegram summary.

Run: python scripts/daily_review.py
Or:  Last step in feature_engineering.yml
"""
from __future__ import annotations
import logging, os, sys, json
from datetime import datetime, date, timedelta, timezone
import psycopg2
import requests
from dotenv import load_dotenv
load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger  = logging.getLogger(__name__)
NEON_URL = os.getenv("CC_POSTGRES_URL")
IST      = timezone(timedelta(hours=5, minutes=30))
SONNET   = "claude-sonnet-4-6"

sys.path.insert(0, os.path.dirname(__file__))


def get_todays_trades() -> list[dict]:
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        cur  = conn.cursor()
        cur.execute("""
            SELECT id, symbol, direction, entry_price, exit_price,
                   quantity, gross_pnl, net_pnl, outcome, hold_minutes,
                   true_gap_pct, gap_type, rvol, sector_rs, sector_chg,
                   final_score, market_direction, vix_level, session,
                   atr, capital_deployed
            FROM investmitra.trade_log
            WHERE trade_date = CURRENT_DATE
            ORDER BY id
        """)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close(); conn.close()
        return rows
    except Exception as e:
        logger.warning("Today's trades: %s", e)
        return []


def get_todays_pnl() -> dict:
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        cur  = conn.cursor()
        cur.execute("""
            SELECT trades, capital_deployed, gross_pnl, brokerage,
                   net_pnl, win_trades, loss_trades, market_direction, vix_level
            FROM investmitra.intraday_pnl
            WHERE trade_date = CURRENT_DATE
        """)
        row = cur.fetchone()
        cur.close(); conn.close()
        if row:
            return {
                "trades":     int(row[0] or 0),
                "capital":    float(row[1] or 0),
                "gross":      float(row[2] or 0),
                "brokerage":  float(row[3] or 0),
                "net":        float(row[4] or 0),
                "wins":       int(row[5] or 0),
                "losses":     int(row[6] or 0),
                "direction":  row[7] or "NEUTRAL",
                "vix":        float(row[8] or 0),
            }
    except Exception as e:
        logger.warning("Today's P&L: %s", e)
    return {}


def call_sonnet(prompt: str) -> str:
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json"},
            json={
                "model":      SONNET,
                "max_tokens": 600,
                "messages":   [{"role": "user", "content": prompt}],
            },
            timeout=30
        )
        data = r.json()
        if data.get("content"):
            return data["content"][0].get("text", "")
        return ""
    except Exception as e:
        logger.error("Sonnet: %s", e)
        return ""


def analyze_day(trades: list[dict], pnl: dict) -> str:
    """Get Sonnet's take on today's trading."""
    if not trades:
        return "No trades today."

    trade_lines = []
    for t in trades:
        trade_lines.append(
            f"{t['direction']} {t['symbol']}: "
            f"gap {float(t.get('true_gap_pct',0)):+.2f}% ({t.get('gap_type','?')}) "
            f"rvol:{float(t.get('rvol',0)):.1f}x "
            f"sector_rs:{float(t.get('sector_rs',0)):.0f} "
            f"pnl:₹{float(t.get('net_pnl',0)):.0f} "
            f"({t.get('outcome','?')} in {t.get('hold_minutes',0)}min)"
        )

    prompt = f"""Review today's intraday trades briefly. Be concise — 3-4 sentences max.

TODAY'S TRADES:
{chr(10).join(trade_lines)}

MARKET: {pnl.get('direction','?')} | VIX: {pnl.get('vix',0):.1f}
NET P&L: ₹{pnl.get('net',0):.0f} | Win rate: {pnl.get('wins',0)}/{pnl.get('trades',0)}

What worked, what didn't, and one key improvement for tomorrow?"""

    return call_sonnet(prompt)


def notify(message: str):
    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        requests.get(
            f"https://api.telegram.org/bot{token}/sendMessage",
            params={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=5
        )
    except: pass


def print_and_notify_summary(trades: list[dict], pnl: dict, ai_review: str):
    today = date.today()

    print(f"\n{'='*65}")
    print(f"  DAILY REVIEW — {today}")
    print(f"{'='*65}")

    if not trades:
        print(f"  No trades today.")
        print(f"  Market: {pnl.get('direction','?')} | VIX: {pnl.get('vix',0):.1f}")
    else:
        print(f"\n  SUMMARY:")
        print(f"  Trades:   {pnl.get('trades',0)} | Wins: {pnl.get('wins',0)} | Losses: {pnl.get('losses',0)}")
        print(f"  Capital:  ₹{pnl.get('capital',0):,.0f}")
        print(f"  Gross:    ₹{pnl.get('gross',0):,.0f}")
        print(f"  Broker:   ₹{pnl.get('brokerage',0):,.0f}")
        print(f"  NET P&L:  ₹{pnl.get('net',0):,.0f}")
        print(f"  Market:   {pnl.get('direction','?')} | VIX: {pnl.get('vix',0):.1f}")

        print(f"\n  TRADES:")
        for t in trades:
            net    = float(t.get('net_pnl', 0))
            emoji  = "✅" if net > 0 else "❌" if net < -100 else "⚠️"
            print(f"  {emoji} {t['direction']} {t['symbol']:<12} "
                  f"₹{float(t.get('entry_price',0)):.0f}→₹{float(t.get('exit_price',0)):.0f} "
                  f"| {t.get('outcome','?'):<12} "
                  f"| Net: ₹{net:.0f}")

        if ai_review:
            print(f"\n  AI REVIEW (Sonnet):")
            print(f"  {ai_review}")

    print(f"{'='*65}\n")

    # Telegram notification
    if not trades:
        notify(f"📊 <b>Daily Review — {today}</b>\n\nNo trades today.\nMarket: {pnl.get('direction','?')} | VIX: {pnl.get('vix',0):.1f}")
        return

    net    = pnl.get('net', 0)
    emoji  = "🟢" if net > 0 else "🔴" if net < 0 else "⚪"
    trades_text = "\n".join([
        f"{'✅' if float(t.get('net_pnl',0))>0 else '❌'} {t['direction']} {t['symbol']} → ₹{float(t.get('net_pnl',0)):.0f} ({t.get('outcome','?')})"
        for t in trades
    ])

    msg = (
        f"{emoji} <b>Daily Review — {today}</b>\n\n"
        f"Trades: {pnl.get('trades',0)} | W:{pnl.get('wins',0)} L:{pnl.get('losses',0)}\n"
        f"Net P&L: ₹{net:,.0f}\n"
        f"Capital: ₹{pnl.get('capital',0):,.0f}\n\n"
        f"{trades_text}\n\n"
    )
    if ai_review:
        msg += f"<i>{ai_review[:300]}</i>"

    notify(msg)


def main():
    print(f"\n🔍 Running daily review for {date.today()}...")

    # Initialize tables if needed
    try:
        from trade_logger import ensure_tables
        ensure_tables()
    except Exception as e:
        logger.warning("Tables: %s", e)

    # Analyze unanalyzed losses
    try:
        from trade_analyzer import analyze_recent_losses
        results = analyze_recent_losses(days=1)
        if results:
            logger.info("Analyzed %d trades today", len(results))
    except Exception as e:
        logger.warning("Trade analysis: %s", e)

    # Get today's data
    trades = get_todays_trades()
    pnl    = get_todays_pnl()

    # AI review
    ai_review = ""
    if trades:
        logger.info("Getting Sonnet daily review...")
        ai_review = analyze_day(trades, pnl)

    # Print + notify
    print_and_notify_summary(trades, pnl, ai_review)


if __name__ == "__main__":
    main()
