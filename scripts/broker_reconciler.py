"""
broker_reconciler.py — Kite broker fill reconciliation for investMITRA v27

Key fixes over v2:
  - Matches by composite key (exchange:symbol:product) throughout
  - persist_engine_position stores exchange and product
  - restore_engine_state() for engine restart recovery
  - DB migration for is_paper column on existing broker_reconciliation table
  - Transaction rollback before retry on psycopg errors
  - Paper positions never compared against real broker exposure
"""
import os, time, logging, psycopg2, json
from decimal import Decimal
from datetime import datetime, date, timedelta, timezone
from dotenv import load_dotenv
load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

IST      = timezone(timedelta(hours=5, minutes=30))
NEON_URL         = os.getenv("CC_POSTGRES_URL")
IS_PAPER         = True    # Set False for live trading
STRATEGY_VERSION = "v28"   # Shared across all investMITRA modules

def _conn():
    return psycopg2.connect(NEON_URL, connect_timeout=10)

def migrate_tables(conn):
    """
    Idempotent migration — creates and upgrades both tables.
    Safe to call on every startup; uses ADD COLUMN IF NOT EXISTS throughout.
    """
    cur = conn.cursor()

    # ── engine_positions ─────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS investmitra.engine_positions (
            id              SERIAL PRIMARY KEY,
            trade_date      DATE          NOT NULL DEFAULT CURRENT_DATE,
            symbol          VARCHAR(30)   NOT NULL,
            exchange        VARCHAR(10)   NOT NULL DEFAULT 'NSE',
            product         VARCHAR(10)   NOT NULL DEFAULT 'MIS',
            direction       VARCHAR(5)    NOT NULL DEFAULT 'LONG',
            entry_price     DECIMAL(12,2) DEFAULT 0,
            orig_qty        INTEGER       DEFAULT 0,
            remaining_qty   INTEGER       DEFAULT 0,
            stop_price      DECIMAL(12,2) DEFAULT 0,
            target_price    DECIMAL(12,2) DEFAULT 0,
            initial_risk    DECIMAL(12,2) DEFAULT 0,
            atr             DECIMAL(10,2) DEFAULT 0,
            trail_level     INTEGER       DEFAULT 0,
            partial_done    BOOLEAN       DEFAULT FALSE,
            partial_gross   DECIMAL(12,2) DEFAULT 0,
            partial_exit_px DECIMAL(12,2),
            estimated_costs DECIMAL(10,2) DEFAULT 80,
            trade_id        VARCHAR(60),
            daily_pnl_at    DECIMAL(12,2) DEFAULT 0,
            trades_today_at INTEGER       DEFAULT 0,
                 daily_brokerage_at    DECIMAL(12,2) DEFAULT 0,
                 consecutive_losses_at INTEGER       DEFAULT 0,
            is_paper        BOOLEAN       DEFAULT TRUE,
            strategy_ver    VARCHAR(20)   DEFAULT 'v28',
            status          VARCHAR(20)   DEFAULT 'OPEN',
            signal_time     TIMESTAMPTZ   DEFAULT NOW(),
            updated_at      TIMESTAMPTZ   DEFAULT NOW()
        )
    """)
    # Upgrade existing table — add any new columns
    new_cols = [
        ("orig_qty",              "INTEGER DEFAULT 0"),
        ("daily_brokerage_at",    "DECIMAL(12,2) DEFAULT 0"),
        ("consecutive_losses_at", "INTEGER DEFAULT 0"),
        # Note: orig_qty backfilled from quantity after column is added
        ("initial_risk",    "DECIMAL(12,2) DEFAULT 0"),
        ("atr",             "DECIMAL(10,2) DEFAULT 0"),
        ("trail_level",     "INTEGER DEFAULT 0"),
        ("partial_exit_px", "DECIMAL(12,2)"),
        ("estimated_costs", "DECIMAL(10,2) DEFAULT 80"),
        ("trades_today_at", "INTEGER DEFAULT 0"),
        ("daily_brokerage_at",    "DECIMAL(12,2) DEFAULT 0"),
        ("consecutive_losses_at", "INTEGER DEFAULT 0"),
        ("signal_time",     "TIMESTAMPTZ DEFAULT NOW()"),
    ]
    for col, defn in new_cols:
        try:
            cur.execute(
                f"ALTER TABLE investmitra.engine_positions "
                f"ADD COLUMN IF NOT EXISTS {col} {defn}"
            )
        except Exception: pass

    # Backfill orig_qty from quantity only if legacy quantity column exists
    try:
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'investmitra'
              AND table_name   = 'engine_positions'
              AND column_name  = 'quantity'
        """)
        if cur.fetchone():
            # Legacy column exists — backfill then we can ignore it
            cur.execute("""
                UPDATE investmitra.engine_positions
                SET orig_qty = quantity
                WHERE (orig_qty IS NULL OR orig_qty = 0)
                  AND quantity IS NOT NULL AND quantity > 0
            """)
            logger.info("Backfilled orig_qty from legacy quantity column")
    except Exception as _e:
        # Transaction may be aborted — rollback before continuing
        try: conn.rollback()
        except: pass
        logger.warning("Backfill skipped: %s", _e)

    # Replace old unique constraint (trade_date, symbol, is_paper) with composite key
    try:
        cur.execute("""
            ALTER TABLE investmitra.engine_positions
            DROP CONSTRAINT IF EXISTS engine_positions_trade_date_symbol_is_paper_key
        """)
    except Exception: pass
    try:
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ep_composite_uidx
            ON investmitra.engine_positions(trade_date, symbol, exchange, product, is_paper)
        """)
    except Exception: pass

    # ── broker_reconciliation ─────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS investmitra.broker_reconciliation (
            id          SERIAL PRIMARY KEY,
            check_time  TIMESTAMPTZ DEFAULT NOW(),
            trade_date  DATE        DEFAULT CURRENT_DATE,
            symbol      VARCHAR(30),
            issue_type  VARCHAR(50),
            engine_val  TEXT,
            broker_val  TEXT,
            is_paper    BOOLEAN     DEFAULT TRUE,
            resolved    BOOLEAN     DEFAULT FALSE
        )
    """)
    cur.execute("""
        ALTER TABLE investmitra.broker_reconciliation
        ADD COLUMN IF NOT EXISTS is_paper BOOLEAN DEFAULT TRUE
    """)

    conn.commit()
    cur.close()

def persist_engine_position(symbol: str, pos: dict,
                             status: str = "OPEN", is_paper: bool = True):
    """
    Persist complete position snapshot on every transition.
    Preserves: original qty, initial_risk, atr, trail_level,
               partial_gross, partial_exit_px, estimated_costs,
               trade_id, signal_time — all unchanged across transitions.
    """
    try:
        conn = _conn()
        migrate_tables(conn)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO investmitra.engine_positions
                (trade_date, symbol, exchange, product, direction,
                 entry_price, orig_qty, remaining_qty,
                 stop_price, target_price,
                 initial_risk, atr, trail_level,
                 partial_done, partial_gross, partial_exit_px,
                 estimated_costs, trade_id,
                 daily_pnl_at, trades_today_at,
                 daily_brokerage_at, consecutive_losses_at,
                 signal_time, is_paper, strategy_ver, status, updated_at)
            VALUES
                (CURRENT_DATE, %s, %s, %s, %s,
                 %s, %s, %s,
                 %s, %s,
                 %s, %s, %s,
                 %s, %s, %s,
                 %s, %s,
                 %s, %s,
                 %s, %s,
                 %s, %s, %s, %s, NOW())
            ON CONFLICT (trade_date, symbol, exchange, product, is_paper)
            DO UPDATE SET
                remaining_qty         = EXCLUDED.remaining_qty,
                stop_price            = EXCLUDED.stop_price,
                trail_level           = EXCLUDED.trail_level,
                partial_done          = EXCLUDED.partial_done,
                partial_gross         = EXCLUDED.partial_gross,
                partial_exit_px       = EXCLUDED.partial_exit_px,
                daily_pnl_at          = EXCLUDED.daily_pnl_at,
                trades_today_at       = EXCLUDED.trades_today_at,
                daily_brokerage_at    = EXCLUDED.daily_brokerage_at,
                consecutive_losses_at = EXCLUDED.consecutive_losses_at,
                status                = EXCLUDED.status,
                updated_at            = NOW()
        """, (
            symbol,
            pos.get("exchange",              "NSE"),
            pos.get("product",               "MIS"),
            pos.get("direction",             "LONG"),
            pos.get("entry",                 0),
            pos.get("orig_qty",              pos.get("qty", pos.get("size", 0))),
            pos.get("remaining_qty",         pos.get("qty", pos.get("size", 0))),
            pos.get("stop",                  0),
            pos.get("target",                0),
            pos.get("initial_risk",          abs(pos.get("entry",0) - pos.get("stop",0))),
            pos.get("atr",                   0),
            pos.get("trail_level",           0),
            pos.get("partial_done",          False),
            pos.get("partial_gross",         0),
            pos.get("partial_exit_px"),
            pos.get("estimated_costs",       80),
            pos.get("trade_id"),
            pos.get("daily_pnl_at",          0),
            pos.get("trades_today_at",       0),
            pos.get("daily_brokerage_at",    0),
            pos.get("consecutive_losses_at", 0),
            pos.get("signal_time",           datetime.now(IST)),
            is_paper,
            STRATEGY_VERSION,
            status,
        ))
        cur.close(); conn.close()
    except Exception as e:
        logger.warning("persist_engine_position(%s): %s", symbol, e)

def restore_engine_state(engine, is_paper: bool = True):
    """
    Restore full engine state after restart.
    Restores (in order):
      1. Daily realised P&L and brokerage from engine_positions snapshots
      2. Trade and loss counters
      3. traded_today set from both sources
      4. All open/partial positions with original values (initial_risk, atr,
         trail_level, estimated_costs, trade_id, signal_time unchanged)
    Raises on failure — caller must block new entries until state is confirmed.
    """
    # Entries remain blocked (set by caller) until this function returns normally
    conn = _conn()
    try:
        migrate_tables(conn)
        cur = conn.cursor()

        # ── 1. Open/partial positions ─────────────────────────────
        cur.execute("""
            SELECT symbol, exchange, product, direction,
                   entry_price, orig_qty, remaining_qty,
                   stop_price, target_price,
                   initial_risk, atr, trail_level,
                   partial_done, partial_gross, partial_exit_px,
                   estimated_costs, trade_id,
                   daily_pnl_at, trades_today_at, signal_time
            FROM investmitra.engine_positions
            WHERE trade_date = CURRENT_DATE
              AND is_paper   = %s
              AND status IN ('OPEN','PARTIAL')
        """, (is_paper,))
        open_rows = cur.fetchall()

        # ── 2. Daily state from most recent snapshot (latest updated_at)
        # Includes gross P&L, brokerage, trade count and loss streak
        cur.execute("""
            SELECT daily_pnl_at, trades_today_at,
                   daily_brokerage_at, consecutive_losses_at
            FROM investmitra.engine_positions
            WHERE trade_date = CURRENT_DATE
              AND is_paper   = %s
            ORDER BY updated_at DESC
            LIMIT 1
        """, (is_paper,))
        row = cur.fetchone()
        realised_pnl        = float(row[0]) if row and row[0] is not None else 0
        trades_today        = int(row[1])   if row and row[1] is not None else 0
        daily_brokerage     = float(row[2]) if row and row[2] is not None else 0
        consecutive_losses  = int(row[3])   if row and row[3] is not None else 0

        # ── 3. traded_today from all today's positions ────────────
        cur.execute("""
            SELECT DISTINCT symbol
            FROM investmitra.engine_positions
            WHERE trade_date = CURRENT_DATE
              AND is_paper   = %s
        """, (is_paper,))
        traded = {r[0] for r in cur.fetchall()}

        cur.close(); conn.close()
    except Exception as e:
        try: conn.rollback(); conn.close()
        except: pass
        raise RuntimeError(f"restore_engine_state DB read failed: {e}") from e

    if not open_rows and not traded:
        logger.info("No state to restore (fresh session)")
        return

    # Apply complete daily state
    engine.risk.daily_pnl          = realised_pnl
    engine.risk.trades_today       = trades_today
    engine.risk.daily_brokerage    = daily_brokerage
    engine.risk.consecutive_losses = consecutive_losses
    logger.info("Restored: P&L=Rs%.2f brokerage=Rs%.2f trades=%d consec_losses=%d",
                realised_pnl, daily_brokerage, trades_today, consecutive_losses)
    logger.info("Restored P&L: Rs%.2f  trades_today: %d", realised_pnl, trades_today)

    # Apply traded_today
    engine.traded_today.update(traded)
    logger.info("Restored traded_today: %d symbols", len(traded))

    missing = []
    for row in open_rows:
        (sym, exch, prod, direction, entry, orig_qty, remaining,
         stop, target, initial_risk, atr, trail_level,
         partial, p_gross, partial_exit_px,
         est_costs, trade_id, daily_pnl_at, trades_today_at,
         signal_time) = row

        f = lambda v, d=0.0: float(v) if v is not None else d
        i = lambda v, d=0:   int(v)   if v is not None else d

        entry        = f(entry)
        stop         = f(stop)
        initial_risk = f(initial_risk) or abs(entry - stop)  # non-zero fallback
        if initial_risk == 0:
            logger.error("Cannot restore %s — initial_risk is zero after breakeven. "
                         "Blocking restart until manually resolved.", sym)
            missing.append(sym)
            continue

        # Restore into risk manager
        engine.risk.positions[sym] = {
            "entry":           entry,
            "stop":            stop,
            "size":            i(remaining) or i(orig_qty),
            "target":          f(target),
            "partial_done":    bool(partial),
            "partial_size":    i(orig_qty) // 2,
            "atr":             f(atr),
            "signal_time":     signal_time or datetime.now(IST),
            "initial_risk":    initial_risk,   # original — never recalculated
            "trail_level":     i(trail_level),
            "direction":       direction,
            "exchange":        exch or "NSE",
            "product":         prod or "MIS",
            "estimated_costs": f(est_costs, 80),
        }

        # Restore signal tracking
        entry_at = signal_time or datetime.now(IST)
        engine.signals[sym] = {
            "symbol":            sym,
            "direction":         direction,
            "entry":             entry,
            "target":            f(target),
            "stoploss":          stop,
            "position_size":     i(orig_qty),
            "partial_done":      bool(partial),
            "partial_gross":     f(p_gross),
            "partial_exit_price": float(partial_exit_px) if partial_exit_px else None,
            "trade_id":          trade_id,
            "estimated_costs":   f(est_costs, 80),
            "entry_at":          entry_at,
            "signal_time":       entry_at,
        }
        engine.traded_today.add(sym)

        # Ensure symbol is monitored (subscribe token if needed)
        if sym not in engine.all_stocks and sym in (engine.long_map or {}):
            engine.all_stocks[sym] = engine.long_map[sym]

        logger.info("Restored: %s %s orig=%dsh rem=%dsh entry=%.2f "
                    "stop=%.2f init_risk=%.2f trail=%d partial=%s",
                    sym, direction, i(orig_qty), i(remaining),
                    entry, stop, initial_risk, i(trail_level), bool(partial))

    if missing:
        raise RuntimeError(
            f"Cannot restore {missing} — zero initial_risk. "
            "Resolve manually before accepting new signals."
        )

    logger.info("State restore complete: %d open positions", len(open_rows))
    # Store restored symbols so main() can subscribe WebSocket tokens
    engine._restored_symbols = [
        {"symbol": row[0], "exchange": row[1] or "NSE", "product": row[2] or "MIS"}
        for row in open_rows
        if row[0] not in missing
    ]

def get_broker_net_positions(kite) -> tuple[dict, bool]:
    """
    Fetch NET positions using composite key exchange:symbol:product.
    Returns (positions_dict, api_ok).
    Empty dict + api_ok=True means genuinely no positions.
    """
    try:
        data    = kite.positions()
        net_pos = data.get("net") or []
        result  = {}
        for p in net_pos:
            sym     = p.get("tradingsymbol", "")
            exch    = p.get("exchange", "NSE")
            product = p.get("product", "MIS")
            qty     = int(p.get("quantity", 0))
            avg_buy  = float(p.get("average_price", 0) or 0)
            avg_sell = float(p.get("sell_price", 0) or 0)
            avg_p    = avg_buy if qty > 0 else avg_sell
            if qty == 0:
                continue
            key = f"{exch}:{sym}:{product}"
            result[key] = {
                "symbol":    sym,
                "exchange":  exch,
                "product":   product,
                "qty":       qty,           # signed
                "avg_price": avg_p,
                "direction": "LONG" if qty > 0 else "SHORT",
                "pnl":       float(p.get("pnl", 0) or 0),
            }
        logger.info("Broker net positions: %d", len(result))
        return result, True
    except Exception as e:
        logger.error("Kite positions API FAILED: %s", e)
        return {}, False

def get_engine_positions(is_paper: bool = True) -> dict | None:
    """
    Read engine open positions from Neon using composite key.
    Returns None on DB failure (distinct from empty dict).
    """
    conn = None
    try:
        conn = _conn()
        migrate_tables(conn)
        cur = conn.cursor()
        cur.execute("""
            SELECT symbol, exchange, product, direction,
                   entry_price,
                   COALESCE(orig_qty, 0) AS orig_qty,
                   COALESCE(remaining_qty, 0) AS remaining_qty,
                   partial_done, status
            FROM investmitra.engine_positions
            WHERE trade_date = CURRENT_DATE
              AND is_paper   = %s
              AND status IN ('OPEN','PARTIAL')
        """, (is_paper,))
        result = {}
        for row in cur.fetchall():
            sym, exch, prod, direction, entry, qty, remaining, partial, status = row
            key = f"{exch}:{sym}:{prod}"
            result[key] = {
                "symbol":    sym,
                "exchange":  exch,
                "product":   prod,
                "direction": direction,
                "entry":     float(entry or 0),
                "qty":       int(qty or 0),
                "remaining": int(remaining or qty or 0),
                "partial":   bool(partial),
                "status":    status,
            }
        cur.close(); conn.close()
        logger.info("Engine positions (paper=%s): %d open", is_paper, len(result))
        return result
    except Exception as e:
        logger.error("Engine positions DB read FAILED: %s", e)
        if conn:
            try: conn.rollback()
            except: pass
            try: conn.close()
            except: pass
        return None   # None = read failure

def reconcile(kite, is_paper: bool = True) -> list:
    """
    Compare engine state vs broker net positions using composite keys.
    Paper positions are NEVER compared against real broker exposure.
    """
    if is_paper:
        # Paper mode: reconcile engine state vs itself (no broker comparison)
        engine_pos = get_engine_positions(is_paper=True)
        if engine_pos is None:
            return [{"symbol":"DB","type":"ENGINE_DB_FAILURE",
                     "engine":"DB read failed","broker":"N/A (paper mode)"}]
        logger.info("Paper mode: %d engine positions tracked", len(engine_pos))
        return []   # No broker discrepancies in paper mode

    # Live mode: full reconciliation
    broker_pos, broker_ok = get_broker_net_positions(kite)
    engine_pos             = get_engine_positions(is_paper=False)

    if not broker_ok:
        return [{"symbol":"API","type":"BROKER_API_FAILURE",
                 "engine":"unknown","broker":"API call failed"}]
    if engine_pos is None:
        return [{"symbol":"DB","type":"ENGINE_DB_FAILURE",
                 "engine":"DB read failed","broker":"unknown"}]

    discrepancies = []

    # Broker has position engine doesn't know about
    for key, bpos in broker_pos.items():
        if key not in engine_pos:
            discrepancies.append({
                "symbol": bpos["symbol"], "type": "BROKER_ONLY",
                "engine": "not tracked",
                "broker": f"{bpos['exchange']}:{bpos['product']} "
                          f"{bpos['direction']} {abs(bpos['qty'])}sh @ {bpos['avg_price']:.2f}",
            })
            logger.warning("BROKER_ONLY: %s", key)

    # Engine has position broker doesn't have
    for key, epos in engine_pos.items():
        if key not in broker_pos:
            discrepancies.append({
                "symbol": epos["symbol"], "type": "ENGINE_ONLY",
                "engine": f"{epos['exchange']}:{epos['product']} "
                          f"{epos['direction']} {epos['remaining']}sh @ {epos['entry']:.2f}",
                "broker": "no matching position",
            })
            logger.warning("ENGINE_ONLY: %s", key)
        else:
            bpos = broker_pos[key]
            # Compare signed quantity
            eng_signed = epos["remaining"] if epos["direction"]=="LONG" else -epos["remaining"]
            brk_signed = bpos["qty"]
            if eng_signed != brk_signed:
                discrepancies.append({
                    "symbol": epos["symbol"], "type": "QUANTITY_MISMATCH",
                    "engine": f"signed {eng_signed:+d}",
                    "broker": f"signed {brk_signed:+d}",
                })
            # Direction mismatch
            if epos["direction"] != bpos["direction"]:
                discrepancies.append({
                    "symbol": epos["symbol"], "type": "DIRECTION_MISMATCH",
                    "engine": epos["direction"], "broker": bpos["direction"],
                })
            # Slippage
            if epos["entry"] > 0:
                slip = abs(bpos["avg_price"] - epos["entry"]) / epos["entry"] * 100
                if slip > 0.5:
                    discrepancies.append({
                        "symbol": epos["symbol"], "type": "HIGH_SLIPPAGE",
                        "engine": f"entry {epos['entry']:.2f}",
                        "broker": f"fill {bpos['avg_price']:.2f} ({slip:.2f}%)",
                    })

    return discrepancies

def save_discrepancies(discrepancies: list, is_paper: bool = True):
    if not discrepancies: return
    conn = None
    try:
        conn = _conn()
        migrate_tables(conn)
        conn.autocommit = True
        cur = conn.cursor()
        for d in discrepancies:
            cur.execute("""
                INSERT INTO investmitra.broker_reconciliation
                    (symbol, issue_type, engine_val, broker_val, is_paper)
                VALUES (%s,%s,%s,%s,%s)
            """, (d["symbol"], d["type"], str(d["engine"]), str(d["broker"]), is_paper))
        cur.close(); conn.close()
    except Exception as e:
        logger.warning("save_discrepancies: %s", e)
        if conn:
            try: conn.rollback(); conn.close()
            except: pass

def print_summary(broker_pos: dict, discrepancies: list, broker_ok: bool, is_paper: bool):
    now  = datetime.now(IST).strftime("%H:%M:%S")
    mode = "PAPER (no broker comparison)" if is_paper else "LIVE"
    print(f"\n  {'='*60}")
    print(f"  BROKER RECONCILIATION [{mode}] — {now}")
    print(f"  {'='*60}")
    if is_paper:
        print(f"  Paper mode — broker positions not compared.")
        return
    if not broker_ok:
        print(f"  ⛔ Broker API FAILED — cannot reconcile")
        return
    print(f"  Broker net positions: {len(broker_pos)}")
    for key, p in broker_pos.items():
        print(f"    {key:<30} {p['direction']:<5} {abs(p['qty']):>4}sh  ₹{p['pnl']:+.0f}")
    if discrepancies:
        print(f"\n  ⚠️  DISCREPANCIES: {len(discrepancies)}")
        for d in discrepancies:
            print(f"    [{d['type']}] {d['symbol']}")
            print(f"      Engine: {d['engine']}")
            print(f"      Broker: {d['broker']}")
    else:
        print(f"\n  ✅ No discrepancies")
    print(f"  {'='*60}\n")

def main():
    logger.info("Broker reconciler started (paper=%s)", IS_PAPER)
    kite = None
    if not IS_PAPER:
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=os.getenv("KITE_API_KEY"))
        kite.set_access_token(os.getenv("KITE_ACCESS_TOKEN"))

    while True:
        now = datetime.now(IST)
        if now.hour < 9 or (now.hour >= 15 and now.minute >= 30):
            time.sleep(60)
            continue
        broker_pos = {}
        broker_ok  = True
        if not IS_PAPER:
            broker_pos, broker_ok = get_broker_net_positions(kite)
        discrepancies = reconcile(kite, IS_PAPER)
        if discrepancies:
            save_discrepancies(discrepancies, IS_PAPER)
        print_summary(broker_pos, discrepancies, broker_ok, IS_PAPER)
        time.sleep(60)

if __name__ == "__main__":
    main()
