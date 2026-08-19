"""
Save daily intraday P&L summary to Neon for Grafana.
Called automatically at end of intraday session.
"""
import psycopg2, os
from datetime import date, datetime, timezone, timedelta
from dotenv import load_dotenv
load_dotenv('.env.prod')

NEON_URL = os.getenv("CC_POSTGRES_URL")
IST      = timezone(timedelta(hours=5, minutes=30))


def ensure_table():
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = True
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS investmitra.intraday_pnl (
            id               SERIAL PRIMARY KEY,
            trade_date       DATE NOT NULL UNIQUE,
            trades           INTEGER DEFAULT 0,
            capital_deployed DECIMAL(12,2) DEFAULT 0,
            gross_pnl        DECIMAL(12,2) DEFAULT 0,
            brokerage        DECIMAL(12,2) DEFAULT 0,
            net_pnl          DECIMAL(12,2) DEFAULT 0,
            win_trades       INTEGER DEFAULT 0,
            loss_trades      INTEGER DEFAULT 0,
            market_direction VARCHAR(20),
            vix_level        DECIMAL(8,2),
            signals          JSONB,
            saved_at         TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.close(); conn.close()


def save_pnl(risk_manager, signals: dict, market_direction: str, vix: float):
    """Save end-of-day P&L to Neon."""
    ensure_table()

    import json
    today = datetime.now(IST).date()

    # Build signals summary
    sig_summary = []
    for sym, sig in signals.items():
        sig_summary.append({
            "symbol":    sym,
            "direction": sig.get("direction"),
            "entry":     sig.get("entry"),
            "target":    sig.get("target"),
            "stoploss":  sig.get("stoploss"),
            "gap":       sig.get("true_gap", sig.get("gap_pct", 0)),
            "cap":       sig.get("cap"),
            "score":     sig.get("final_score"),
        })

    # Capital deployed = sum of (position_size × entry) for all signals
    capital = sum(s.get("position_size", 0) * s.get("entry", 0) for s in signals.values())

    # Win/loss count from consecutive losses tracking
    wins   = risk_manager.trades_today - risk_manager.consecutive_losses
    losses = risk_manager.consecutive_losses

    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = True
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO investmitra.intraday_pnl
            (trade_date, trades, capital_deployed, gross_pnl, brokerage,
             net_pnl, win_trades, loss_trades, market_direction, vix_level, signals)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (trade_date) DO UPDATE SET
            trades=EXCLUDED.trades, capital_deployed=EXCLUDED.capital_deployed,
            gross_pnl=EXCLUDED.gross_pnl, brokerage=EXCLUDED.brokerage,
            net_pnl=EXCLUDED.net_pnl, win_trades=EXCLUDED.win_trades,
            loss_trades=EXCLUDED.loss_trades, signals=EXCLUDED.signals,
            saved_at=NOW()
    """, (
        today,
        risk_manager.trades_today,
        round(capital, 2),
        round(risk_manager.daily_pnl, 2),
        round(risk_manager.daily_brokerage, 2),
        round(risk_manager.net_pnl, 2),
        max(wins, 0), losses,
        market_direction,
        vix,
        json.dumps(sig_summary)
    ))
    cur.close(); conn.close()
    print(f"\n  💾 Daily P&L saved to Neon for {today}")
