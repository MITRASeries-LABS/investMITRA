"""
investMITRA — Order Manager (Semi-Auto)
Monitors your Kite positions and manages exits automatically.

YOU:    Place BUY/SELL entry on Kite app (30 sec)
SYSTEM: Detects fill → places SL + target → manages exits

NOT linked to signal generation — purely manages open positions.

Run in Terminal 2:
  python scripts/order_manager.py

Telegram alerts sent to your phone for every event.
"""
from __future__ import annotations
import os, sys, time, logging
from datetime import datetime, date, timedelta, timezone
from collections import defaultdict
import psycopg2
import requests
from dotenv import load_dotenv
load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

from kiteconnect import KiteConnect

API_KEY         = os.getenv("KITE_API_KEY")
ACCESS_TOKEN    = os.getenv("KITE_ACCESS_TOKEN")
NEON_URL        = os.getenv("CC_POSTGRES_URL")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT   = os.getenv("TELEGRAM_CHAT_ID")
IST             = timezone(timedelta(hours=5, minutes=30))

# ── Risk Parameters ────────────────────────────────────────────────────────────
MAX_CAPITAL_PER_TRADE  = 25000
MAX_DAILY_LOSS_INR     = 6000
MAX_POSITIONS          = 3
BROKERAGE_PER_TRADE    = 80
SQUAREOFF_HOUR         = 15   # 3:00 PM
SQUAREOFF_MINUTE       = 0
POLL_INTERVAL_SEC      = 5    # Check positions every 5 seconds


# ── Telegram Notifications ─────────────────────────────────────────────────────

def notify(message: str, silent: bool = False):
    """Send Telegram alert to phone."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        logger.info("Telegram not configured — %s", message)
        return
    try:
        requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            params={
                "chat_id":              TELEGRAM_CHAT,
                "text":                 message,
                "parse_mode":           "HTML",
                "disable_notification": silent,
            },
            timeout=5
        )
    except Exception as e:
        logger.warning("Telegram failed: %s", e)


# ── Signal Store (load from Neon — signals saved by intraday_signals.py) ──────

def load_todays_signals() -> dict:
    """Load today's signals from intraday_pnl table."""
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        cur  = conn.cursor()
        cur.execute("""
            SELECT signals FROM investmitra.intraday_pnl
            WHERE trade_date = CURRENT_DATE
        """)
        row = cur.fetchone()
        cur.close(); conn.close()
        if row and row[0]:
            import json
            sigs = row[0] if isinstance(row[0], list) else json.loads(row[0])
            return {s["symbol"]: s for s in sigs}
    except Exception as e:
        logger.warning("Load signals: %s", e)
    return {}


# ── Order Tracking ─────────────────────────────────────────────────────────────

class PositionTracker:
    def __init__(self):
        self.managed     = {}    # symbol -> management state
        self.daily_pnl   = 0.0
        self.brokerage   = 0.0
        self.order_log   = []

    @property
    def net_pnl(self):
        return self.daily_pnl - self.brokerage

    def is_managed(self, symbol: str) -> bool:
        return symbol in self.managed

    def start_managing(self, symbol: str, entry: float, qty: int,
                       stop: float, target: float, direction: str):
        self.managed[symbol] = {
            "entry":        entry,
            "qty":          qty,
            "stop":         stop,
            "target":       target,
            "direction":    direction,
            "sl_order_id":  None,
            "tgt_order_id": None,
            "partial_done": False,
            "partial_qty":  qty // 2,
            "start_time":   datetime.now(IST),
        }
        self.brokerage += BROKERAGE_PER_TRADE
        logger.info("Managing %s: entry=%.2f stop=%.2f target=%.2f qty=%d",
                    symbol, entry, stop, target, qty)

    def stop_managing(self, symbol: str):
        if symbol in self.managed:
            del self.managed[symbol]


# ── Order Placement ────────────────────────────────────────────────────────────

def place_sl_order(kite: KiteConnect, symbol: str, qty: int,
                   stop: float, direction: str) -> str | None:
    """Place stoploss market order."""
    try:
        txn = kite.TRANSACTION_TYPE_SELL if direction=="LONG" else kite.TRANSACTION_TYPE_BUY
        order_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NSE,
            tradingsymbol=symbol,
            transaction_type=txn,
            quantity=qty,
            order_type=kite.ORDER_TYPE_SLM,
            trigger_price=round(stop, 1),
            product=kite.PRODUCT_MIS,
        )
        logger.info("SL order placed: %s qty=%d trigger=%.2f id=%s", symbol, qty, stop, order_id)
        return order_id
    except Exception as e:
        logger.error("SL order failed %s: %s", symbol, e)
        notify(f"⚠️ SL ORDER FAILED — {symbol}\nError: {e}\nManually set SL @ ₹{stop:.2f}")
        return None


def place_target_order(kite: KiteConnect, symbol: str, qty: int,
                       target: float, direction: str) -> str | None:
    """Place limit target order."""
    try:
        txn = kite.TRANSACTION_TYPE_SELL if direction=="LONG" else kite.TRANSACTION_TYPE_BUY
        order_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NSE,
            tradingsymbol=symbol,
            transaction_type=txn,
            quantity=qty,
            order_type=kite.ORDER_TYPE_LIMIT,
            price=round(target, 1),
            product=kite.PRODUCT_MIS,
        )
        logger.info("Target order placed: %s qty=%d price=%.2f id=%s", symbol, qty, target, order_id)
        return order_id
    except Exception as e:
        logger.error("Target order failed %s: %s", symbol, e)
        return None


def cancel_order(kite: KiteConnect, order_id: str, symbol: str):
    """Cancel an existing order."""
    try:
        kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id)
        logger.info("Cancelled order %s for %s", order_id, symbol)
    except Exception as e:
        logger.warning("Cancel failed %s: %s", order_id, e)


def market_exit(kite: KiteConnect, symbol: str, qty: int, direction: str) -> bool:
    """Place market order to exit position."""
    try:
        txn = kite.TRANSACTION_TYPE_SELL if direction=="LONG" else kite.TRANSACTION_TYPE_BUY
        kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NSE,
            tradingsymbol=symbol,
            transaction_type=txn,
            quantity=qty,
            order_type=kite.ORDER_TYPE_MARKET,
            product=kite.PRODUCT_MIS,
        )
        logger.info("Market exit: %s qty=%d", symbol, qty)
        return True
    except Exception as e:
        logger.error("Market exit failed %s: %s", symbol, e)
        notify(f"🚨 EXIT FAILED — {symbol} qty={qty}\nManually square off NOW!\nError: {e}")
        return False


# ── Main Monitor Loop ──────────────────────────────────────────────────────────

def get_ltp(kite: KiteConnect, symbol: str) -> float:
    try:
        q = kite.quote([f"NSE:{symbol}"])
        return float(q.get(f"NSE:{symbol}", {}).get("last_price", 0))
    except:
        return 0


def run_order_manager(kite: KiteConnect, signals: dict, tracker: PositionTracker):
    """Main loop — runs every 5 seconds during market hours."""

    logger.info("Order manager started — monitoring positions")
    notify("🤖 <b>investMITRA Order Manager LIVE</b>\nMonitoring positions every 5 sec\nSignals loaded: " + str(len(signals)))

    squaredoff = False

    while True:
        now     = datetime.now(IST)
        mkt_min = now.hour * 60 + now.minute

        # Market closed
        if mkt_min < 9*60+15 or mkt_min >= 15*60+30:
            logger.info("Market closed — sleeping")
            time.sleep(60)
            continue

        # 3:00 PM — square off everything
        if now.hour == SQUAREOFF_HOUR and now.minute >= SQUAREOFF_MINUTE and not squaredoff:
            logger.info("3:00 PM — squaring off all positions")
            notify("⏰ <b>3:00 PM — Squaring off all positions</b>")
            _squareoff_all(kite, tracker)
            squaredoff = True
            _save_final_pnl(tracker)
            break

        # Get current positions from Kite
        try:
            positions = kite.positions().get("net", [])
        except Exception as e:
            logger.warning("Positions fetch failed: %s", e)
            time.sleep(POLL_INTERVAL_SEC)
            continue

        # Daily loss check
        if tracker.net_pnl <= -MAX_DAILY_LOSS_INR:
            logger.warning("Daily loss limit hit — squaring off")
            notify(f"🚨 <b>DAILY LOSS LIMIT HIT</b>\nNet P&L: ₹{tracker.net_pnl:.0f}\nSquaring off all positions")
            _squareoff_all(kite, tracker)
            squaredoff = True
            break

        # Process each open position
        for pos in positions:
            symbol = pos.get("tradingsymbol", "")
            qty    = int(pos.get("quantity", 0))
            avg    = float(pos.get("average_price", 0))

            if qty == 0 or not symbol:
                continue

            direction = "LONG" if qty > 0 else "SHORT"
            qty = abs(qty)

            # New position detected — start managing
            if not tracker.is_managed(symbol):
                # Get signal data if available
                sig = signals.get(symbol, {})
                if sig:
                    stop   = sig.get("stoploss", 0)
                    target = sig.get("target", 0)
                else:
                    # No signal data — use ATR-based defaults
                    ltp    = get_ltp(kite, symbol)
                    atr    = ltp * 0.015  # 1.5% fallback
                    stop   = round(avg - atr*1.5, 1) if direction=="LONG" else round(avg + atr*1.5, 1)
                    target = round(avg + atr*3.0, 1) if direction=="LONG" else round(avg - atr*3.0, 1)

                tracker.start_managing(symbol, avg, qty, stop, target, direction)

                # Place SL and target orders
                sl_id  = place_sl_order(kite, symbol, qty, stop, direction)
                tgt_id = place_target_order(kite, symbol, qty, target, direction)

                tracker.managed[symbol]["sl_order_id"]  = sl_id
                tracker.managed[symbol]["tgt_order_id"] = tgt_id

                capital = round(avg * qty, 0)
                notify(
                    f"🟢 <b>POSITION DETECTED — {symbol}</b>\n"
                    f"Direction: {direction}\n"
                    f"Entry: ₹{avg:.2f} × {qty} shares = ₹{capital:,.0f}\n"
                    f"Stop: ₹{stop:.2f}\n"
                    f"Target: ₹{target:.2f}\n"
                    f"SL order: {'placed ✅' if sl_id else 'FAILED ❌'}\n"
                    f"Target order: {'placed ✅' if tgt_id else 'FAILED ❌'}"
                )

            else:
                # Already managing — check for partial exit
                state  = tracker.managed[symbol]
                entry  = state["entry"]
                stop   = state["stop"]
                risk   = abs(entry - stop)
                ltp    = get_ltp(kite, symbol)

                if not state["partial_done"] and ltp > 0:
                    hit_1r = (direction=="LONG" and ltp >= entry + risk) or \
                             (direction=="SHORT" and ltp <= entry - risk)

                    if hit_1r:
                        # Cancel existing orders
                        if state["sl_order_id"]:
                            cancel_order(kite, state["sl_order_id"], symbol)
                        if state["tgt_order_id"]:
                            cancel_order(kite, state["tgt_order_id"], symbol)

                        # Exit half position at market
                        half_qty = state["partial_qty"]
                        market_exit(kite, symbol, half_qty, direction)

                        # Remaining qty
                        rem_qty = qty - half_qty
                        new_stop = entry  # Move to breakeven

                        # Place new SL at breakeven for remaining
                        new_sl = place_sl_order(kite, symbol, rem_qty, new_stop, direction)
                        # Place new target for remaining
                        new_tgt = place_target_order(kite, symbol, rem_qty, state["target"], direction)

                        state["partial_done"]  = True
                        state["sl_order_id"]   = new_sl
                        state["tgt_order_id"]  = new_tgt
                        state["stop"]          = new_stop

                        pnl_partial = risk * half_qty
                        tracker.daily_pnl += pnl_partial

                        notify(
                            f"💰 <b>PARTIAL EXIT — {symbol}</b>\n"
                            f"Sold {half_qty} shares @ ₹{ltp:.2f}\n"
                            f"P&L: +₹{pnl_partial:.0f}\n"
                            f"Stop moved to breakeven ₹{new_stop:.2f}\n"
                            f"Remaining: {rem_qty} shares\n"
                            f"Net P&L today: ₹{tracker.net_pnl:.0f}"
                        )

        # Check if any managed position was closed (hit SL or target)
        open_symbols = {p["tradingsymbol"] for p in positions if abs(int(p.get("quantity",0))) > 0}
        for symbol in list(tracker.managed.keys()):
            if symbol not in open_symbols:
                state = tracker.managed[symbol]
                ltp   = get_ltp(kite, symbol)
                pnl   = (ltp - state["entry"]) * state["qty"] if state["direction"]=="LONG" else \
                        (state["entry"] - ltp) * state["qty"]
                tracker.daily_pnl += pnl
                tracker.stop_managing(symbol)

                result = "✅ TARGET HIT" if pnl > 0 else "❌ STOPLOSS HIT"
                notify(
                    f"{result} — <b>{symbol}</b>\n"
                    f"P&L: {'+'if pnl>=0 else ''}₹{pnl:.0f}\n"
                    f"Net P&L today: ₹{tracker.net_pnl:.0f}"
                )

        time.sleep(POLL_INTERVAL_SEC)


def _squareoff_all(kite: KiteConnect, tracker: PositionTracker):
    """Cancel all orders and square off all positions."""
    try:
        # Cancel all open orders
        orders = kite.orders()
        for order in orders:
            if order.get("status") in ("TRIGGER PENDING", "OPEN"):
                try:
                    kite.cancel_order(
                        variety=order.get("variety", kite.VARIETY_REGULAR),
                        order_id=order["order_id"]
                    )
                    logger.info("Cancelled order: %s", order["order_id"])
                except: pass

        # Square off all net positions
        positions = kite.positions().get("net", [])
        for pos in positions:
            qty    = int(pos.get("quantity", 0))
            symbol = pos.get("tradingsymbol", "")
            if qty == 0 or not symbol: continue

            direction = "LONG" if qty > 0 else "SHORT"
            market_exit(kite, symbol, abs(qty), direction)

        logger.info("All positions squared off")

    except Exception as e:
        logger.error("Squareoff failed: %s", e)
        notify(f"🚨 AUTO SQUAREOFF FAILED\nManually close all positions!\nError: {e}")


def _save_final_pnl(tracker: PositionTracker):
    """Update Neon with final P&L."""
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        conn.autocommit = True
        cur  = conn.cursor()
        cur.execute("""
            UPDATE investmitra.intraday_pnl
            SET gross_pnl=gross_pnl + %s,
                brokerage=brokerage + %s,
                net_pnl=net_pnl + %s,
                saved_at=NOW()
            WHERE trade_date=CURRENT_DATE
        """, (round(tracker.daily_pnl,2),
              round(tracker.brokerage,2),
              round(tracker.net_pnl,2)))
        cur.close(); conn.close()

        notify(
            f"📊 <b>DAY COMPLETE — {date.today()}</b>\n"
            f"Gross P&L:  ₹{tracker.daily_pnl:.0f}\n"
            f"Brokerage:  ₹{tracker.brokerage:.0f}\n"
            f"Net P&L:    ₹{tracker.net_pnl:.0f}\n"
            f"Grafana updated ✅"
        )
        logger.info("Final P&L saved to Neon")
    except Exception as e:
        logger.warning("Final P&L save: %s", e)


def main():
    if not API_KEY or not ACCESS_TOKEN:
        print("❌ Run: python scripts/kite_login.py first")
        sys.exit(1)

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        print("⚠️  Telegram not configured — alerts disabled")
        print("   Add to .env.prod:")
        print("   TELEGRAM_BOT_TOKEN=your_bot_token")
        print("   TELEGRAM_CHAT_ID=your_chat_id")
        print()

    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(ACCESS_TOKEN)

    # Load today's signals
    signals = load_todays_signals()
    logger.info("Loaded %d signals for today", len(signals))

    tracker = PositionTracker()

    print(f"\n{'='*60}")
    print(f"  investMITRA ORDER MANAGER — {date.today()}")
    print(f"{'='*60}")
    print(f"  Monitoring Kite positions every {POLL_INTERVAL_SEC} seconds")
    print(f"  Max daily loss: ₹{MAX_DAILY_LOSS_INR}")
    print(f"  Auto square-off: 3:00 PM")
    print(f"  Telegram: {'✅ configured' if TELEGRAM_TOKEN else '❌ not configured'}")
    print(f"  Signals loaded: {len(signals)}")
    print(f"{'='*60}")
    print(f"  Waiting for you to place entry orders on Kite app...")
    print(f"  System will auto-place SL + target after detection")
    print(f"{'='*60}\n")

    try:
        run_order_manager(kite, signals, tracker)
    except KeyboardInterrupt:
        logger.info("Stopped by user")
        notify("⚠️ Order manager stopped manually — check open positions!")


if __name__ == "__main__":
    main()
