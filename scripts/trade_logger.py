"""
investMITRA — Trade Logger
Saves every trade with full market context to Neon.
Called by order_manager.py when a position closes.

Table: investmitra.trade_log
"""
from __future__ import annotations
import logging, os, json
from datetime import datetime, date, timedelta, timezone
import psycopg2
from psycopg2.extras import Json
from dotenv import load_dotenv
load_dotenv('.env.prod')

logger  = logging.getLogger(__name__)
NEON_URL = os.getenv("CC_POSTGRES_URL")
IST      = timezone(timedelta(hours=5, minutes=30))


def ensure_tables():
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = True
    cur  = conn.cursor()

    # Trade log — every trade with full context
    cur.execute("""
        CREATE TABLE IF NOT EXISTS investmitra.trade_log (
            id               SERIAL PRIMARY KEY,
            trade_date       DATE NOT NULL,
            symbol           VARCHAR(20) NOT NULL,
            direction        VARCHAR(10) NOT NULL,
            entry_price      DECIMAL(12,2),
            exit_price       DECIMAL(12,2),
            quantity         INTEGER,
            gross_pnl        DECIMAL(12,2),
            net_pnl          DECIMAL(12,2),
            outcome          VARCHAR(20),  -- TARGET_HIT, SL_HIT, TIME_EXIT, REVERSAL, PARTIAL
            hold_minutes     INTEGER,
            -- Signal context at entry
            true_gap_pct     DECIMAL(8,4),
            gap_type         VARCHAR(30),
            rvol             DECIMAL(8,2),
            vwap_score       DECIMAL(8,2),
            orb_score        DECIMAL(8,2),
            sector_rs        DECIMAL(8,2),
            sector_chg       DECIMAL(8,4),
            breadth_score    DECIMAL(8,2),
            kl_score         DECIMAL(8,2),
            sentiment        DECIMAL(8,4),
            final_score      DECIMAL(8,2),
            quality_score    DECIMAL(8,2),
            opp_score        DECIMAL(8,2),
            -- Market context
            market_direction VARCHAR(20),
            vix_level        DECIMAL(8,2),
            nifty_chg        DECIMAL(8,4),
            session          VARCHAR(20),
            -- Additional
            atr              DECIMAL(10,2),
            stop_dist        DECIMAL(10,2),
            capital_deployed DECIMAL(12,2),
            piotroski        INTEGER,
            screen_count     INTEGER,
            market_cap       VARCHAR(10),
            -- Full signal JSON for AI analysis
            signal_json      JSONB,
            created_at       TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Trade insights — AI analysis per trade
    cur.execute("""
        CREATE TABLE IF NOT EXISTS investmitra.trade_insights (
            id               SERIAL PRIMARY KEY,
            trade_id         INTEGER REFERENCES investmitra.trade_log(id),
            trade_date       DATE,
            symbol           VARCHAR(20),
            outcome          VARCHAR(20),
            ai_model         VARCHAR(50),
            analysis         TEXT,
            issues_found     JSONB,  -- list of issues identified
            suggestions      JSONB,  -- parameter adjustment suggestions
            confidence       DECIMAL(4,2),
            created_at       TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Signal weights — current optimized weights
    cur.execute("""
        CREATE TABLE IF NOT EXISTS investmitra.signal_weights (
            id               SERIAL PRIMARY KEY,
            effective_date   DATE NOT NULL UNIQUE,
            weights          JSONB NOT NULL,
            updated_by       VARCHAR(50),  -- 'weekly_opus', 'monthly_opus', 'manual'
            trade_count      INTEGER,
            win_rate         DECIMAL(5,2),
            notes            TEXT,
            created_at       TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Insert default weights if none exist
    cur.execute("SELECT COUNT(*) FROM investmitra.signal_weights")
    if cur.fetchone()[0] == 0:
        default_weights = {
            "gap_score":     0.15,
            "rvol_score":    0.13,
            "vwap_score":    0.10,
            "orb_score":     0.12,
            "holding_score": 0.10,
            "sector_rs":     0.12,
            "breadth_score": 0.05,
            "regime_score":  0.05,
            "kl_score":      0.08,
            "sent_score":    0.04,
            "bulk_score":    0.04,
            "preopen_score": 0.03,
            # Thresholds
            "gap_threshold_momentum":  0.30,
            "gap_threshold_choppy":    0.60,
            "gap_threshold_afternoon": 0.40,
            "rvol_min_continuation":   1.50,
            "skip_choppy_session":     False,
            "skip_fade_risk":          False,
            "min_sector_chg_long":     -0.50,
            "max_sector_chg_short":    0.50,
        }
        cur.execute("""
            INSERT INTO investmitra.signal_weights
                (effective_date, weights, updated_by, notes)
            VALUES (CURRENT_DATE, %s, 'default', 'Initial default weights')
        """, (Json(default_weights),))
        logger.info("Default weights inserted")

    cur.close(); conn.close()
    logger.info("Trade log tables ready")


def log_trade(
    symbol: str,
    direction: str,
    entry_price: float,
    exit_price: float,
    quantity: int,
    outcome: str,
    hold_minutes: int,
    signal: dict,
    market_direction: str,
    vix_level: float,
    brokerage: float = 80.0,
) -> int:
    """
    Log a completed trade to Neon.
    Returns trade_id for AI analysis.
    """
    ensure_tables()

    gross_pnl = (exit_price - entry_price) * quantity
    if direction == "SHORT":
        gross_pnl = -gross_pnl
    net_pnl = gross_pnl - brokerage

    details  = signal.get("details", {})
    capital  = entry_price * quantity

    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=15)
        conn.autocommit = True
        cur  = conn.cursor()

        cur.execute("""
            INSERT INTO investmitra.trade_log (
                trade_date, symbol, direction, entry_price, exit_price,
                quantity, gross_pnl, net_pnl, outcome, hold_minutes,
                true_gap_pct, gap_type, rvol, vwap_score, orb_score,
                sector_rs, sector_chg, breadth_score, kl_score,
                sentiment, final_score, quality_score, opp_score,
                market_direction, vix_level, session,
                atr, stop_dist, capital_deployed,
                piotroski, screen_count, market_cap, signal_json
            ) VALUES (
                CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            ) RETURNING id
        """, (
            symbol, direction, entry_price, exit_price,
            quantity, round(gross_pnl,2), round(net_pnl,2), outcome, hold_minutes,
            signal.get("true_gap"), details.get("gap_type"),
            details.get("rvol"), details.get("vwap_score"),
            details.get("orb_score"), details.get("sector_rs"),
            details.get("sector_chg"), details.get("breadth"),
            details.get("kl_score"), details.get("sentiment"),
            signal.get("final_score"), signal.get("quality_score"),
            signal.get("opp_score"),
            market_direction, vix_level, signal.get("session"),
            signal.get("atr"), signal.get("stop_dist"), round(capital,2),
            signal.get("piotroski"), signal.get("screens"),
            signal.get("cap"), Json(signal)
        ))

        trade_id = cur.fetchone()[0]
        cur.close(); conn.close()

        logger.info("Trade logged: %s %s entry=%.2f exit=%.2f pnl=%.2f id=%d",
                    direction, symbol, entry_price, exit_price, net_pnl, trade_id)
        return trade_id

    except Exception as e:
        logger.error("Trade log failed: %s", e)
        return -1


def get_current_weights() -> dict:
    """Load current signal weights from Neon."""
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        cur  = conn.cursor()
        cur.execute("""
            SELECT weights FROM investmitra.signal_weights
            ORDER BY effective_date DESC LIMIT 1
        """)
        row = cur.fetchone()
        cur.close(); conn.close()
        if row:
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])
    except Exception as e:
        logger.warning("Weights fetch: %s", e)
    return {}


def get_recent_trades(days: int = 7) -> list[dict]:
    """Get recent trades for analysis."""
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        cur  = conn.cursor()
        cur.execute("""
            SELECT id, trade_date, symbol, direction,
                   entry_price, exit_price, quantity,
                   gross_pnl, net_pnl, outcome, hold_minutes,
                   true_gap_pct, gap_type, rvol, sector_rs,
                   sector_chg, final_score, market_direction,
                   vix_level, session
            FROM investmitra.trade_log
            WHERE trade_date >= CURRENT_DATE - INTERVAL '%s days'
            ORDER BY created_at DESC
        """ % days)
        cols = [d[0] for d in cur.description]
        trades = [dict(zip(cols, row)) for row in cur.fetchall()]
        cur.close(); conn.close()
        return trades
    except Exception as e:
        logger.warning("Recent trades: %s", e)
        return []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ensure_tables()
    print("Trade log tables created successfully")
    weights = get_current_weights()
    print(f"Current weights loaded: {len(weights)} parameters")
