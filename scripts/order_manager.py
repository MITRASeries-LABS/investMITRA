"""
investMITRA — Order Manager v2 (Semi-Auto)
Uses LIMIT orders instead of market orders to reduce slippage.

LONG entry:  limit at signal_price + 0.1% (ensures fill without chasing)
SHORT entry: limit at signal_price - 0.1%
SL:         SL-M order (market when triggered)
Target:     Limit order at exact target price

YOU:    Place BUY/SELL entry on Kite app (30 sec)
SYSTEM: Detects fill → places SL + target → manages exits
"""
from __future__ import annotations
import logging, os, sys, time, json
from datetime import datetime, date, timedelta, timezone
import psycopg2
import requests
from dotenv import load_dotenv
load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

from kiteconnect import KiteConnect

API_KEY        = os.getenv("KITE_API_KEY")
ACCESS_TOKEN   = os.getenv("KITE_ACCESS_TOKEN")
NEON_URL       = os.getenv("CC_POSTGRES_URL")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT  = os.getenv("TELEGRAM_CHAT_ID")
IST            = timezone(timedelta(hours=5, minutes=30))

MAX_CAPITAL_PER_TRADE = 25000
MAX_DAILY_LOSS_INR    = 6000
MAX_POSITIONS         = 3
BROKERAGE_PER_TRADE   = 80
SQUAREOFF_HOUR        = 15
SQUAREOFF_MINUTE      = 0
POLL_INTERVAL_SEC     = 5

# Limit order buffer
LIMIT_BUFFER_PCT      = 0.001   # 0.1% buffer for limit orders


def notify(message: str, silent: bool = False):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        logger.info("Telegram: %s", message[:50])
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
        logger.warning("Telegram: %s", e)


def load_todays_signals() -> dict:
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        cur  = conn.cursor()
        cur.execute("SELECT signals FROM investmitra.intraday_pnl WHERE trade_date=CURRENT_DATE")
        row = cur.fetchone()
        cur.close(); conn.close()
        if row and row[0]:
            sigs = row[0] if isinstance(row[0], list) else json.loads(row[0])
            return {s["symbol"]: s for s in sigs}
    except Exception as e:
        logger.warning("Load signals: %s", e)
    return {}


def place_sl_order(kite, symbol, qty, stop, direction) -> str | None:
    """Place SL-M order — triggers at stop price, executes at market."""
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
        logger.info("SL-M placed: %s qty=%d trigger=%.2f", symbol, qty, stop)
        return order_id
    except Exception as e:
        logger.error("SL order failed %s: %s", symbol, e)
        notify(f"⚠️ SL FAILED — {symbol}\nSet manually @ ₹{stop:.2f}\nError: {e}")
        return None


def place_target_order(kite, symbol, qty, target, direction) -> str | None:
    """Place LIMIT order at exact target price."""
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
        logger.info("Limit target placed: %s qty=%d price=%.2f", symbol, qty, target)
        return order_id
    except Exception as e:
        logger.error("Target order failed %s: %s", symbol, e)
        return None


def place_limit_exit(kite, symbol, qty, price, direction) -> str | None:
    """
    Place LIMIT order for partial exit at 1R.
    Uses limit instead of market to avoid slippage.
    Buffer: 0.1% worse than ideal to ensure fill.
    """
    try:
        txn = kite.TRANSACTION_TYPE_SELL if direction=="LONG" else kite.TRANSACTION_TYPE_BUY
        # For LONG partial exit: limit slightly below 1R price to ensure fill
        # For SHORT partial exit: limit slightly above 1R price
        if direction == "LONG":
            limit_price = round(price * (1 - LIMIT_BUFFER_PCT), 1)
        else:
            limit_price = round(price * (1 + LIMIT_BUFFER_PCT), 1)

        order_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NSE,
            tradingsymbol=symbol,
            transaction_type=txn,
            quantity=qty,
            order_type=kite.ORDER_TYPE_LIMIT,
            price=limit_price,
            product=kite.PRODUCT_MIS,
        )
        logger.info("Limit partial exit: %s qty=%d price=%.2f", symbol, qty, limit_price)
        return order_id
    except Exception as e:
        logger.error("Limit exit failed %s: %s", symbol, e)
        return None


def market_exit(kite, symbol, qty, direction) -> bool:
    """Market exit — only used for 3PM square off and emergencies."""
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
        notify(f"🚨 EXIT FAILED — {symbol}\nManually square off {qty} shares!\nError: {e}")
        return False


def cancel_order(kite, order_id, symbol):
    try:
        kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id)
        logger.info("Cancelled %s for %s", order_id, symbol)
    except Exception as e:
        logger.warning("Cancel failed %s: %s", order_id, e)


def get_ltp(kite, symbol) -> float:
    try:
        q = kite.quote([f"NSE:{symbol}"])
        return float(q.get(f"NSE:{symbol}", {}).get("last_price", 0))
    except:
        return 0


class PositionTracker:
    def __init__(self):
        self.managed   = {}
        self.daily_pnl = 0.0
        self.brokerage = 0.0

    @property
    def net_pnl(self): return self.daily_pnl - self.brokerage

    def can_trade(self) -> tuple[bool, str]:
        if self.net_pnl <= -MAX_DAILY_LOSS_INR:
            return False, f"Daily loss ₹{self.net_pnl:.0f}"
        if len(self.managed) >= MAX_POSITIONS:
            return False, f"Max positions ({len(self.managed)})"
        return True, "OK"

    def start_managing(self, symbol, entry, qty, stop, target, direction):
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
            "trail_level":  0,
        }
        self.brokerage += BROKERAGE_PER_TRADE

    def stop_managing(self, symbol):
        if symbol in self.managed:
            del self.managed[symbol]


def run_order_manager(kite, signals, tracker):
    logger.info("Order manager v2 started — LIMIT orders enabled")
    notify(
        "🤖 <b>investMITRA Order Manager v2 LIVE</b>\n"
        "Using LIMIT orders (reduced slippage)\n"
        f"Signals: {len(signals)} | Max positions: {MAX_POSITIONS}"
    )

    squaredoff = False

    while True:
        now     = datetime.now(IST)
        mkt_min = now.hour * 60 + now.minute

        if mkt_min < 9*60+15 or mkt_min >= 15*60+30:
            time.sleep(60); continue

        # 3:00 PM square off
        if now.hour == SQUAREOFF_HOUR and now.minute >= SQUAREOFF_MINUTE and not squaredoff:
            logger.info("3:00 PM — squaring off")
            notify("⏰ <b>3:00 PM — Squaring off all positions</b>")
            _squareoff_all(kite, tracker)
            squaredoff = True
            _save_final_pnl(tracker)
            break

        # Daily loss check
        if tracker.net_pnl <= -MAX_DAILY_LOSS_INR:
            notify(f"🚨 <b>DAILY LOSS LIMIT</b>\nNet: ₹{tracker.net_pnl:.0f}\nSquaring off")
            _squareoff_all(kite, tracker)
            squaredoff = True
            break

        try:
            positions = kite.positions().get("net", [])
        except Exception as e:
            logger.warning("Positions: %s", e)
            time.sleep(POLL_INTERVAL_SEC); continue

        # New position detected
        for pos in positions:
            symbol = pos.get("tradingsymbol", "")
            qty    = int(pos.get("quantity", 0))
            avg    = float(pos.get("average_price", 0))
            if qty == 0 or not symbol: continue

            direction = "LONG" if qty > 0 else "SHORT"
            qty = abs(qty)

            if not tracker.managed.get(symbol):
                sig    = signals.get(symbol, {})
                stop   = sig.get("stoploss", 0)
                target = sig.get("target", 0)

                if not stop or not target:
                    ltp    = get_ltp(kite, symbol)
                    atr    = ltp * 0.015
                    stop   = round(avg-atr*1.5,1) if direction=="LONG" else round(avg+atr*1.5,1)
                    target = round(avg+atr*3.0,1) if direction=="LONG" else round(avg-atr*3.0,1)

                tracker.start_managing(symbol, avg, qty, stop, target, direction)

                # Place SL-M + Limit target
                sl_id  = place_sl_order(kite, symbol, qty, stop, direction)
                tgt_id = place_target_order(kite, symbol, qty, target, direction)

                tracker.managed[symbol]["sl_order_id"]  = sl_id
                tracker.managed[symbol]["tgt_order_id"] = tgt_id

                capital = round(avg * qty, 0)
                notify(
                    f"{'🟢' if direction=='LONG' else '🔴'} <b>POSITION — {symbol}</b>\n"
                    f"Direction: {direction}\n"
                    f"Entry: ₹{avg:.2f} × {qty} = ₹{capital:,.0f}\n"
                    f"SL-M: ₹{stop:.2f} {'✅' if sl_id else '❌'}\n"
                    f"Target (Limit): ₹{target:.2f} {'✅' if tgt_id else '❌'}\n"
                    f"Orders: LIMIT (low slippage)"
                )

            else:
                # Manage existing position
                state    = tracker.managed[symbol]
                entry    = state["entry"]
                stop     = state["stop"]
                risk     = abs(entry - stop)
                ltp      = get_ltp(kite, symbol)
                is_long  = direction == "LONG"

                if not state["partial_done"] and ltp > 0:
                    hit_1r = (is_long and ltp >= entry+risk) or \
                             (not is_long and ltp <= entry-risk)

                    if hit_1r:
                        # Cancel existing orders
                        if state["sl_order_id"]:
                            cancel_order(kite, state["sl_order_id"], symbol)
                        if state["tgt_order_id"]:
                            cancel_order(kite, state["tgt_order_id"], symbol)

                        half_qty   = state["partial_qty"]
                        one_r_price = entry + risk if is_long else entry - risk

                        # LIMIT order for partial exit (not market)
                        place_limit_exit(kite, symbol, half_qty, one_r_price, direction)

                        rem_qty  = qty - half_qty
                        new_stop = entry  # Breakeven

                        # New SL at breakeven + new target for remainder
                        new_sl  = place_sl_order(kite, symbol, rem_qty, new_stop, direction)
                        new_tgt = place_target_order(kite, symbol, rem_qty, state["target"], direction)

                        state["partial_done"]  = True
                        state["sl_order_id"]   = new_sl
                        state["tgt_order_id"]  = new_tgt
                        state["stop"]          = new_stop
                        state["qty"]           = rem_qty

                        pnl_partial = risk * half_qty
                        tracker.daily_pnl += pnl_partial

                        notify(
                            f"💰 <b>PARTIAL EXIT (Limit) — {symbol}</b>\n"
                            f"{half_qty} shares @ ₹{one_r_price:.2f} (limit)\n"
                            f"P&L: +₹{pnl_partial:.0f}\n"
                            f"Stop → breakeven ₹{new_stop:.2f}\n"
                            f"Remaining: {rem_qty} shares\n"
                            f"Net today: ₹{tracker.net_pnl:.0f}"
                        )

                # Trailing stop
                elif state["partial_done"] and state["qty"] > 0 and ltp > 0:
                    moves = int((ltp-entry)/risk) if direction=="LONG" else int((entry-ltp)/risk)
                    if moves > state["trail_level"] + 1:
                        state["trail_level"] = moves - 1
                        new_stop = round(entry+(state["trail_level"]*risk*0.5),1) if direction=="LONG" else \
                                   round(entry-(state["trail_level"]*risk*0.5),1)
                        if (direction=="LONG" and new_stop>state["stop"]) or \
                           (direction=="SHORT" and new_stop<state["stop"]):
                            if state["sl_order_id"]:
                                cancel_order(kite, state["sl_order_id"], symbol)
                            new_sl = place_sl_order(kite, symbol, state["qty"], new_stop, direction)
                            state["stop"]       = new_stop
                            state["sl_order_id"]= new_sl
                            print(f"\n  📈 TRAILING STOP: {symbol} → ₹{new_stop:.2f}\n")

        # Check closed positions
        open_syms = {p["tradingsymbol"] for p in positions if abs(int(p.get("quantity",0)))>0}
        for symbol in list(tracker.managed.keys()):
            if symbol not in open_syms:
                state = tracker.managed[symbol]
                ltp   = get_ltp(kite, symbol)
                pnl   = (ltp-state["entry"])*state["qty"] if state["direction"]=="LONG" else \
                        (state["entry"]-ltp)*state["qty"]
                tracker.daily_pnl += pnl
                tracker.stop_managing(symbol)
                result = "✅ TARGET" if pnl>0 else "❌ STOPLOSS"
                notify(
                    f"{result} — <b>{symbol}</b>\n"
                    f"P&L: {'+'if pnl>=0 else ''}₹{pnl:.0f}\n"
                    f"Net today: ₹{tracker.net_pnl:.0f}"
                )

        time.sleep(POLL_INTERVAL_SEC)


def _squareoff_all(kite, tracker):
    try:
        orders = kite.orders()
        for o in orders:
            if o.get("status") in ("TRIGGER PENDING","OPEN"):
                try:
                    kite.cancel_order(
                        variety=o.get("variety", kite.VARIETY_REGULAR),
                        order_id=o["order_id"]
                    )
                except: pass

        positions = kite.positions().get("net", [])
        for pos in positions:
            qty    = int(pos.get("quantity", 0))
            symbol = pos.get("tradingsymbol", "")
            if qty == 0 or not symbol: continue
            direction = "LONG" if qty > 0 else "SHORT"
            market_exit(kite, symbol, abs(qty), direction)
    except Exception as e:
        logger.error("Squareoff: %s", e)
        notify(f"🚨 SQUAREOFF FAILED — Close manually!\nError: {e}")


def _save_final_pnl(tracker):
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        conn.autocommit = True
        cur  = conn.cursor()
        cur.execute("""
            UPDATE investmitra.intraday_pnl SET
                gross_pnl=gross_pnl+%s, brokerage=brokerage+%s,
                net_pnl=net_pnl+%s, saved_at=NOW()
            WHERE trade_date=CURRENT_DATE
        """, (round(tracker.daily_pnl,2), round(tracker.brokerage,2), round(tracker.net_pnl,2)))
        cur.close(); conn.close()
        notify(
            f"📊 <b>DAY COMPLETE — {date.today()}</b>\n"
            f"Gross: ₹{tracker.daily_pnl:.0f}\n"
            f"Brokerage: ₹{tracker.brokerage:.0f}\n"
            f"Net P&L: ₹{tracker.net_pnl:.0f}\n"
            f"Grafana updated ✅"
        )
    except Exception as e:
        logger.warning("Save P&L: %s", e)


def main():
    if not API_KEY or not ACCESS_TOKEN:
        print("❌ Run: python scripts/kite_login.py first")
        sys.exit(1)

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        print("⚠️  Telegram not configured")

    kite    = KiteConnect(api_key=API_KEY)
    kite.set_access_token(ACCESS_TOKEN)
    signals = load_todays_signals()
    tracker = PositionTracker()

    print(f"\n{'='*60}")
    print(f"  investMITRA ORDER MANAGER v2 — {date.today()}")
    print(f"  LIMIT orders enabled (reduced slippage)")
    print(f"{'='*60}")
    print(f"  Entry SL:    SL-M (market when triggered)")
    print(f"  Target:      Limit at exact price")
    print(f"  Partial:     Limit at 1R price")
    print(f"  Square off:  Market at 3:00 PM")
    print(f"  Signals:     {len(signals)}")
    print(f"  Telegram:    {'✅' if TELEGRAM_TOKEN else '❌'}")
    print(f"{'='*60}")
    print(f"  Waiting for positions on Kite app...")
    print(f"{'='*60}\n")

    try:
        run_order_manager(kite, signals, tracker)
    except KeyboardInterrupt:
        logger.info("Stopped")
        notify("⚠️ Order manager stopped — check open positions!")


if __name__ == "__main__":
    main()
