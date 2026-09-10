"""investMITRA automatic execution v3.

Imported by intraday_signals.py. Defaults to simulated execution at the engine
boundary. No orders or notifications are sent at import time. A single worker
owns broker mutations; a durable intent is committed before every submission.
"""
from __future__ import annotations

import copy
import json
import logging
import math
import os
import queue
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
logger = logging.getLogger(__name__)
TERMINAL = {"COMPLETE", "CANCELLED", "REJECTED"}
PREFIX = "IM3"


def notify(message: str, silent: bool = False):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        logger.info("%s", message)
        return
    try:
        import requests
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": message,
                            "disable_notification": silent}, timeout=5).raise_for_status()
    except Exception:
        logger.warning("Telegram delivery failed; see local execution log")


def async_notify(message):
    # Broker protection must not wait for a Telegram HTTP response.
    threading.Thread(target=notify, args=(message,), daemon=True).start()


def tick_round(value, tick, upwards):
    value, tick = Decimal(str(value)), Decimal(str(tick))
    if not value.is_finite() or not tick.is_finite() or tick <= 0 or value <= 0:
        raise ValueError("Invalid price or instrument tick size")
    return float((value / tick).to_integral_value(
        rounding=ROUND_CEILING if upwards else ROUND_FLOOR) * tick)


def key(symbol, exchange="NSE", product="MIS"):
    return f"{exchange}:{symbol}:{product}"


class Journal:
    """One local machine/process only. Keep the file across restarts.

    A separate OS file lock survives SQLite commits and prevents two local
    workers using the same journal. Do not share this journal over a network FS.
    """
    def __init__(self, path, account, mode):
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lockfile = open(str(path) + ".lock", "a+b")
        self.lockfile.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                self.lockfile.write(b"0"); self.lockfile.flush(); self.lockfile.seek(0)
                msvcrt.locking(self.lockfile.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            self.lockfile.close()
            raise
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS history (day TEXT PRIMARY KEY, body TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS simulator (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
        row = self.db.execute("SELECT body FROM state WHERE id=1").fetchone()
        self.state = json.loads(row[0]) if row else {
            "version": 3, "account": account, "mode": mode, "day": None,
            "trades": {}, "halt": "", "flatten": False}
        if (self.state.get("version"), self.state.get("account"), self.state.get("mode")) != (3, account, mode):
            self.close()
            raise RuntimeError("Journal version/account/mode does not match")
        self.save()

    def save(self):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES (1, ?)",
                            (json.dumps(self.state, allow_nan=False),))

    def close(self):
        if hasattr(self, "db"):
            self.db.close()
        if getattr(self, "lockfile", None):
            self.lockfile.close()


class KiteBroker:
    """Only this adapter can send live orders. Activation is explicit."""
    mode = "live"

    def __init__(self, kite, account):
        if os.getenv("INVESTMITRA_LIVE_TRADING") != "YES":
            raise RuntimeError("Live orders disabled: INVESTMITRA_LIVE_TRADING is not YES")
        if not account or kite.profile().get("user_id") != account:
            raise RuntimeError("KITE_USER_ID must match the authenticated broker account")
        import inspect
        signature = inspect.signature(kite.place_order)
        if "market_protection" not in signature.parameters and not any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()):
            raise RuntimeError("Installed Kite SDK lacks market_protection; update SDK before live activation")
        self.kite = kite

    def orders(self): return self.kite.orders()
    def positions(self): return self.kite.positions()["net"]
    def quotes(self, symbols): return self.kite.quote(["NSE:" + s for s in symbols])
    def place(self, params): return self.kite.place_order(**params)
    def cancel(self, order_id): return self.kite.cancel_order(variety="regular", order_id=order_id)
    def modify(self, order_id, **params):
        return self.kite.modify_order(variety="regular", order_id=order_id, **params)


class PaperBroker:
    """Deterministic execution simulator, NOT a market liquidity/backtest model.

    Reads live quotes, but never calls Kite order methods. Simulated orders are
    persisted for restart tests. Crossing orders fill fully at the quoted LTP.
    """
    mode = "auto_paper"

    def __init__(self, kite, journal):
        self.kite, self.journal = kite, journal
        row = journal.db.execute("SELECT body FROM simulator WHERE id=1").fetchone()
        self.book = json.loads(row[0]) if row else []
        self.last = {}

    def _save(self):
        with self.journal.db:
            self.journal.db.execute("INSERT OR REPLACE INTO simulator VALUES (1, ?)",
                                   (json.dumps(self.book),))

    def quotes(self, symbols):
        quotes = self.kite.quote(["NSE:" + s for s in symbols])
        self.last.update(quotes)
        for order in self.book:
            self._match(order)
        self._save()
        return quotes

    def _match(self, order):
        if order["status"] in TERMINAL:
            return
        quote = self.last.get("NSE:" + order["tradingsymbol"], {})
        px = float(quote.get("last_price", 0))
        if px <= 0: return
        buy = order["transaction_type"] == "BUY"
        typ = order["order_type"]
        cross = typ == "MARKET"
        if typ == "LIMIT": cross = px <= order["price"] if buy else px >= order["price"]
        if typ == "SL-M": cross = px >= order["trigger_price"] if buy else px <= order["trigger_price"]
        if cross:
            order.update(status="COMPLETE", filled_quantity=order["quantity"], average_price=px)
        elif order.get("validity") == "IOC":
            order["status"] = "CANCELLED"

    def place(self, params):
        order = dict(params, order_id="P" + uuid.uuid4().hex[:16],
                     status="TRIGGER PENDING" if params["order_type"] == "SL-M" else "OPEN",
                     filled_quantity=0, average_price=0)
        self.book.append(order); self._match(order); self._save()
        return order["order_id"]

    def orders(self): return copy.deepcopy(self.book)

    def positions(self):
        result = {}
        for o in self.book:
            k = key(o["tradingsymbol"], o["exchange"], o["product"])
            p = result.setdefault(k, dict(tradingsymbol=o["tradingsymbol"], exchange=o["exchange"], product=o["product"], quantity=0))
            p["quantity"] += o["filled_quantity"] * (1 if o["transaction_type"] == "BUY" else -1)
        return list(result.values())

    def cancel(self, order_id):
        for o in self.book:
            if o["order_id"] == order_id and o["status"] not in TERMINAL:
                o["status"] = "CANCELLED"
        self._save()

    def modify(self, order_id, **params):
        for o in self.book:
            if o["order_id"] == order_id and o["status"] not in TERMINAL:
                o.update(params); self._match(o)
        self._save()


class AutoOrderManager:
    """Serial execution state machine. All public broker work runs in step()."""
    def __init__(self, broker, journal, instruments, *, daily_cap=25000,
                 min_ticket=1000, cost_reserve=80, max_risk=2000,
                 max_daily_loss=6000, max_positions=3, max_losses=2,
                 clock=lambda: datetime.now(IST), alerts=async_notify):
        self.broker, self.journal, self.state = broker, journal, journal.state
        self.meta = {i["tradingsymbol"]: i for i in instruments
                     if i.get("exchange", "NSE") == "NSE" and i.get("segment") == "NSE"}
        self.daily_cap, self.min_ticket, self.cost_reserve = daily_cap, min_ticket, cost_reserve
        self.max_risk, self.max_daily_loss = max_risk, max_daily_loss
        self.max_positions, self.max_losses = max_positions, max_losses
        self.clock, self.alerts = clock, alerts
        self.inbox = queue.Queue(maxsize=100)
        self.lock = threading.Lock()
        self.view = {"ready": False, "reason": "Broker recovery pending", "trades": {}, "remaining": 0}
        self.orders, self.broker_positions, self.quotes = [], {}, {}
        self.ready = False
        self.external_activity = False
        self.flatten_requested = threading.Event()
        self.stop_requested = threading.Event()
        self._last_error = None
        today = self.clock().date().isoformat()
        if self.state["day"] not in (None, today):
            if any(self._remaining(t) or self._active(t) for t in self.state["trades"].values()):
                raise RuntimeError("Previous-day unfinished execution: reconcile manually; daily order book has expired")
            with journal.db:
                journal.db.execute("INSERT OR REPLACE INTO history VALUES (?, ?)",
                                   (self.state["day"], json.dumps(self.state, allow_nan=False)))
            self.state.update(day=today, trades={}, halt="", flatten=False)
            if isinstance(broker, PaperBroker):
                broker.book = []; broker._save()
        self.state["day"] = today
        self.journal.save()

    def snapshot(self):
        with self.lock: return copy.deepcopy(self.view)

    def offer(self, signal):
        try:
            clean = json.loads(json.dumps(signal, default=lambda x: x.isoformat(), allow_nan=False))
            self.inbox.put_nowait(clean)
            return True
        except queue.Full: return False

    def request_flatten(self): self.flatten_requested.set()

    def _actions(self, trade): return trade["orders"]
    def _active(self, trade): return [o for o in trade["orders"] if o["status"] not in TERMINAL]
    def _entry(self, trade): return trade["orders"][0]
    def _filled(self, trade): return self._entry(trade)["filled"]
    def _exited(self, trade): return sum(o["filled"] for o in trade["orders"][1:])
    def _remaining(self, trade): return self._filled(trade) - self._exited(trade)
    def _entry_price(self, trade): return self._entry(trade)["average"]
    def _cost(self, trade):
        # Provisional conservative allowance, not broker contract-note charges.
        return self.cost_reserve if self._filled(trade) else 0
    def _gross(self, trade):
        exits = sum(o["filled"] * o["average"] for o in trade["orders"][1:])
        return (exits - self._entry_price(trade) * self._exited(trade)) * trade["sign"]
    def _budget_used(self):
        value = 0
        for t in self.state["trades"].values():
            e = self._entry(t)
            value += e["filled"] * e["average"]
            if e["status"] not in TERMINAL:
                value += (e["qty"] - e["filled"]) * t["reservation_price"] + self.cost_reserve
            else:
                value += self._cost(t)
        return round(value, 2)

    def _halt(self, reason):
        if self.state["halt"] != reason:
            self.state["halt"] = reason
            self.journal.save()
            self.alerts("investMITRA entries halted: " + reason)

    def _protection_pending(self):
        for trade in self.state["trades"].values():
            remaining = self._remaining(trade)
            if remaining and not any(o["kind"] == "STOP" and o["status"] == "TRIGGER PENDING"
                                     and o["qty"]-o["filled"] >= remaining for o in trade["orders"]):
                return True
        return False

    def _publish(self):
        trades = copy.deepcopy(self.state["trades"])
        gross = sum(self._gross(t) for t in trades.values())
        costs = sum(self._cost(t) for t in trades.values())
        closed = sorted((t for t in trades.values() if t.get("closed_at") and self._filled(t)), key=lambda t:t["closed_at"])
        losses = 0
        for t in closed:
            losses = losses + 1 if self._gross(t) - self._cost(t) <= 0 else 0
        exposure = sum(self._remaining(t) * self._entry_price(t) for t in trades.values())
        with self.lock:
            self.view = dict(ready=self.ready and not self.state["halt"] and not self.state["flatten"] and not self._protection_pending(),
                             reason=self.state["halt"], trades=trades,
                             remaining=max(0, self.daily_cap - self._budget_used()),
                             tickets=sum(self._filled(t)*self._entry_price(t) for t in trades.values()),
                             gross=gross, costs=costs, net=gross-costs, losses=losses,
                             exposure=exposure, flat=self.ready and all(not self._remaining(t) and not self._active(t) for t in trades.values()),
                             mode=self.broker.mode, account=self.state["account"], day=self.state["day"])

    def _fresh(self, quote):
        try:
            ts = quote["timestamp"]
            if isinstance(ts, str): ts = datetime.fromisoformat(ts)
            if ts.tzinfo is None: ts = ts.replace(tzinfo=IST)
            age = (self.clock() - ts).total_seconds()
            return -2 <= age <= 10 and float(quote["last_price"]) > 0
        except (KeyError, ValueError, TypeError): return False

    def _refresh(self):
        self.orders = self.broker.orders()
        positions = self.broker.positions()
        if not isinstance(self.orders, list) or not isinstance(positions, list):
            raise RuntimeError("Malformed broker order/position response")
        self.broker_positions = {}
        for p in positions:
            k = key(p["tradingsymbol"], p["exchange"], p["product"])
            if k in self.broker_positions: raise RuntimeError("Duplicate broker position key")
            self.broker_positions[k] = int(p["quantity"])
        by_tag = {}
        for o in self.orders:
            by_tag.setdefault(o.get("tag"), []).append(o)
        known = {o["tag"] for t in self.state["trades"].values() for o in t["orders"]}
        if any(str(o.get("tag", "")).startswith(PREFIX) and o.get("tag") not in known for o in self.orders):
            raise RuntimeError("Broker has investMITRA orders missing from this journal; restore the correct journal")
        # Other MIS activity consumes an untracked part of the user's daily
        # allowance. Block new entries, but continue managing owned positions.
        self.external_activity = any(
            o.get("product") == "MIS" and o.get("tag") not in known
            and (int(o.get("filled_quantity", 0)) > 0 or o.get("status") not in TERMINAL)
            for o in self.orders)
        if self.external_activity:
            self._halt("Other intraday orders exist in this account; daily budget cannot be isolated")
        uncertain = False
        for t in self.state["trades"].values():
            for action in t["orders"]:
                matches = by_tag.get(action["tag"], [])
                if len(matches) > 1: raise RuntimeError("Duplicate broker orders for one execution intent")
                if not matches:
                    if action["status"] not in TERMINAL:
                        uncertain = True
                    continue
                o = matches[0]
                expected = action["params"]
                for field in ("tradingsymbol", "exchange", "product", "transaction_type", "quantity"):
                    if o.get(field) != expected[field]:
                        raise RuntimeError("Order identity/quantity differs from durable intent")
                filled = int(o["filled_quantity"])
                avg = float(o.get("average_price") or 0)
                if not 0 <= filled <= action["qty"] or filled < action["filled"] or (filled and (not math.isfinite(avg) or avg <= 0)):
                    raise RuntimeError("Invalid or regressing fill report")
                action.update(order_id=o["order_id"], status=o["status"], filled=filled, average=avg,
                              message=o.get("status_message", ""))
                if o.get("trigger_price") is not None:
                    action["broker_trigger"] = float(o["trigger_price"])
            if self._remaining(t) < 0: raise RuntimeError("Exit fills exceed owned entry quantity")
            if self._filled(t) and not self._remaining(t) and not self._active(t) and not t.get("closed_at"):
                t["closed_at"] = self.clock().isoformat()
                self.alerts(f"{self.broker.mode}: {t['symbol']} CLOSED, gross Rs{self._gross(t):.2f}; charges provisional")
        self.journal.save()
        if uncertain: raise RuntimeError("Submission outcome unknown; waiting for broker tag reconciliation (no resubmission)")
        for t in self.state["trades"].values():
            k = key(t["symbol"])
            expected = self._remaining(t) * t["sign"]
            # Never adopt external trades, including same-symbol manual changes.
            if self.broker_positions.get(k, 0) != expected:
                raise RuntimeError(f"Owned position mismatch for {k}; reconcile before further order changes")
            if any(key(o["tradingsymbol"], o["exchange"], o["product"]) == k
                   and o.get("tag") not in known and o["status"] not in TERMINAL for o in self.orders):
                raise RuntimeError(f"External pending order conflicts with {k}")
        self.ready = True

    def _submit(self, trade, kind, qty, *, price=None, trigger=None, market=False):
        if qty <= 0: raise ValueError("Cannot submit zero quantity")
        params = dict(variety="regular", exchange="NSE", tradingsymbol=trade["symbol"],
                      product="MIS", quantity=int(qty),
                      transaction_type=("BUY" if trade["sign"] > 0 else "SELL") if kind == "ENTRY" else ("SELL" if trade["sign"] > 0 else "BUY"),
                      order_type="SL-M" if kind == "STOP" else ("MARKET" if market else "LIMIT"),
                      validity="DAY" if kind == "STOP" or market else "IOC",
                      tag=PREFIX + uuid.uuid4().hex[:17])
        if trigger is not None: params["trigger_price"] = trigger
        if price is not None: params["price"] = price
        if kind == "STOP" or market: params["market_protection"] = -1
        action = dict(kind=kind, tag=params["tag"], params=params, qty=int(qty), filled=0,
                      average=0, status="PREPARED", order_id=None, created=self.clock().isoformat(), cancels=0)
        trade["orders"].append(action)
        self.journal.save()  # reservation + intent committed before broker call
        try:
            action["order_id"] = self.broker.place(params)
            action["status"] = "SUBMITTED"
        except Exception:
            action["status"] = "UNKNOWN"  # timeout/rejection exception is NOT proof of no order
            self.ready = False
            logger.exception("Order response uncertain; will reconcile tag")
        self.journal.save()
        return action

    def _cancel(self, action):
        if action["status"] in TERMINAL or not action["order_id"]: return
        if action["cancels"] >= 3:
            self._halt("Cancellation still unconfirmed; inspect broker order " + action["order_id"])
            return
        action["cancels"] += 1
        self.journal.save()
        try: self.broker.cancel(action["order_id"])
        except Exception: logger.warning("Cancel response uncertain; broker status will be checked")
        # Crucially do NOT change order status or place a replacement here.

    def _accept(self, sig):
        now = self.clock()
        if not self.ready or self.state["halt"] or self.state["flatten"] or not (570 <= now.hour*60+now.minute < 900): return
        if self._protection_pending(): return
        symbol = sig["symbol"]
        if symbol in self.state["trades"] or symbol not in self.meta: return
        if (now.timestamp() - float(sig["offered_at"])) > 10: return
        quote = self.quotes.get("NSE:" + symbol, {})
        if not self._fresh(quote): return
        if self.broker_positions.get(key(symbol), 0): return
        if any(key(o["tradingsymbol"], o["exchange"], o["product"]) == key(symbol)
               and o["status"] not in TERMINAL for o in self.orders): return
        view = self.snapshot()
        count = sum(bool(self._remaining(t) or self._active(t)) for t in self.state["trades"].values())
        if count >= self.max_positions or view.get("losses", 0) >= self.max_losses: return
        sign = 1 if sig["direction"] == "LONG" else -1
        ltp, tick = float(quote["last_price"]), float(self.meta[symbol]["tick_size"])
        if abs(ltp / float(sig["entry"]) - 1) > 0.003: return  # no chasing stale signals
        limit = tick_round(ltp * (1.001 if sign > 0 else .999), tick, sign > 0)
        # BUY limit bounds entry spend. A SHORT can fill above its sell limit:
        # reserve the exchange upper circuit instead to bound gross ticket value.
        reserve_price = limit if sign > 0 else float(quote.get("upper_circuit_limit") or 0)
        if reserve_price < limit: return
        stop = tick_round(float(sig["stoploss"]), tick, sign > 0)
        if sign * (limit - stop) <= 0: return
        risk_per_share = abs(reserve_price - stop) if sign > 0 else abs(stop - limit)
        remaining = self.daily_cap - self._budget_used() - self.cost_reserve
        qty = min(int(sig["position_size"]), int(remaining / reserve_price),
                  int(self.max_risk / risk_per_share))
        if qty <= 0 or qty * limit < self.min_ticket: return
        open_risk = sum(max(0, (self._entry_price(t)-t["stop"])*t["sign"]) * self._remaining(t)
                        for t in self.state["trades"].values())
        if view.get("net", 0) - open_risk - qty*risk_per_share - self.cost_reserve <= -self.max_daily_loss: return
        if sign > 0 and qty*limit + self.cost_reserve > self.daily_cap + min(0,view.get("net",0))-view.get("exposure",0): return
        trade = dict(symbol=symbol, sign=sign, signal=copy.deepcopy(sig), orders=[],
                     reservation_price=reserve_price, stop=stop, initial_risk=None,
                     partial_done=False, exit_goal=0, exit_reason="", exit_attempts=0,
                     entry_at=None, closed_at=None, below_open_at=None)
        self.state["trades"][symbol] = trade
        self._submit(trade, "ENTRY", qty, price=limit)
        self.alerts(f"{self.broker.mode}: {symbol} ENTRY SUBMITTED {qty} shares, limit Rs{limit:.2f}; awaiting fill")
        # Do not accept another candidate against the same broker snapshot.
        self.ready = False

    def _goal(self, t, goal, reason):
        if goal > t["exit_goal"]:
            t.update(exit_goal=goal, exit_reason=reason, exit_attempts=0)
            self.journal.save()

    def _manage(self, t):
        entry_order = self._entry(t)
        if entry_order["status"] not in TERMINAL:
            age = (self.clock() - datetime.fromisoformat(entry_order["created"])).total_seconds()
            if self._filled(t) or age >= 15 or self.state["flatten"]: self._cancel(entry_order)
            return
        qty, remaining = self._filled(t), self._remaining(t)
        if not qty: return
        if not t["entry_at"]:
            entry = self._entry_price(t)
            t["entry_at"] = self.clock().isoformat()
            t["initial_risk"] = max(float(self.meta[t["symbol"]]["tick_size"]), abs(entry-t["stop"]))
            self.journal.save()
        if not remaining: return
        quote = self.quotes.get("NSE:" + t["symbol"], {})
        fresh = self._fresh(quote)
        px = float(quote.get("last_price") or 0)
        entry, risk, sign = self._entry_price(t), t["initial_risk"], t["sign"]
        tick = float(self.meta[t["symbol"]]["tick_size"])
        if any(o["kind"] == "STOP" and o["filled"] for o in t["orders"]):
            self._goal(t, qty, "STOPLOSS")
        if self.state["flatten"]:
            self._goal(t, qty, "SQUAREOFF")
        elif fresh:
            move = (px-entry)*sign
            half = qty // 2
            if not t["partial_done"] and move >= risk:
                if half: self._goal(t, half, "PARTIAL_1R")
                else: t["partial_done"] = True
            if half and self._exited(t) >= half: t["partial_done"] = True
            if t["partial_done"]:
                levels = max(0, int(move / risk) - 1)
                desired = tick_round(entry + sign*levels*risk*.5, tick, sign > 0)
                if (desired - t["stop"])*sign > 0: t["stop"] = desired
            opened = datetime.fromisoformat(t["entry_at"])
            minutes = (self.clock()-opened).total_seconds()/60
            today_open = float(t["signal"].get("today_open") or entry)
            gap_intact = (px-today_open)*sign >= 0
            if minutes > 40 and not t["partial_done"] and (move < 0 or (not gap_intact and minutes >= 120)):
                self._goal(t, qty, "DEAD_TRADE")
            below_open = sign > 0 and px < today_open*.995 and px < entry*.998
            if below_open:
                t["below_open_at"] = t["below_open_at"] or self.clock().isoformat()
                if (self.clock()-datetime.fromisoformat(t["below_open_at"])).total_seconds() >= 600:
                    self._goal(t, qty, "REVERSAL")
            else: t["below_open_at"] = None
            if (px-t["stop"])*sign <= 0: self._goal(t, qty, "STOPLOSS")
        active = [o for o in self._active(t) if o["kind"] != "ENTRY"]
        if len(active) > 1: raise RuntimeError("Multiple active exit orders; manual reconciliation required")
        self.journal.save()
        goal_remaining = max(0, t["exit_goal"] - self._exited(t))
        if active:
            o = active[0]
            if o["kind"] == "STOP":
                if goal_remaining and not t.get("manual_required"):
                    self._cancel(o)  # wait for terminal status, then recompute remaining fills
                elif o["status"] == "TRIGGER PENDING" and o.get("broker_trigger") != t["stop"]:
                    # No replacement order; modify same ID. Resolve unknown response by polling.
                    try: self.broker.modify(o["order_id"], trigger_price=t["stop"])
                    except Exception: logger.warning("Stop modification unconfirmed; checking broker next cycle")
            elif (self.clock()-datetime.fromisoformat(o["created"])).total_seconds() >= 15:
                self._cancel(o)
            return
        if t.get("manual_required"):
            # Keep one protective stop while the operator resolves failed exits.
            if not active and not any(o["kind"] == "STOP" and o["status"] == "REJECTED" for o in t["orders"]):
                self._submit(t, "STOP", remaining, trigger=t["stop"])
            return
        if goal_remaining:
            if t["exit_attempts"] >= 3:
                t["manual_required"] = True
                self._halt("Exit retries exhausted for " + t["symbol"] + "; inspect and close via Kite")
                if not any(o["kind"] == "STOP" and o["status"] == "REJECTED" for o in t["orders"]):
                    self._submit(t, "STOP", remaining, trigger=t["stop"])
                return
            t["exit_attempts"] += 1
            full_exit = t["exit_goal"] == qty
            if fresh:
                price = tick_round(px*(.999 if sign > 0 else 1.001), tick, sign < 0)
                self._submit(t, "EXIT", min(remaining, goal_remaining), price=price)
            elif full_exit:
                # Scheduled/emergency exit remains active even without market ticks.
                self._submit(t, "EXIT", min(remaining, goal_remaining), market=True)
            else:
                t["exit_attempts"] -= 1
                self._submit(t, "STOP", remaining, trigger=t["stop"])
            return
        failed_stops = sum(o["kind"] == "STOP" and o["status"] == "REJECTED" for o in t["orders"])
        if failed_stops:
            self._halt("Protective stop rejected for " + t["symbol"] + "; attempting full exit")
            self._goal(t, qty, "PROTECTION_FAILED")
            return
        self._submit(t, "STOP", remaining, trigger=t["stop"])

    def step(self):
        self.ready = False
        try:
            if self.clock().date().isoformat() != self.state["day"]:
                raise RuntimeError("Session date changed; restart only after reconciling previous session")
            if self.flatten_requested.is_set() or self.clock().hour*60+self.clock().minute >= 900:
                self.state["flatten"] = True
                self.journal.save()
            pending = []
            while not self.inbox.empty():
                try: pending.append(self.inbox.get_nowait())
                except queue.Empty: break
            pending = list({s["symbol"]: s for s in pending}.values())
            symbols = set(self.state["trades"]) | {s["symbol"] for s in pending}
            if symbols:
                try: self.quotes = self.broker.quotes(sorted(symbols))
                except Exception: self.quotes = {}  # broker-side protection remains; timed exits still run
            self._refresh()
            self._publish()
            unrealised = sum((float(self.quotes.get("NSE:"+t["symbol"], {}).get("last_price") or self._entry_price(t))-self._entry_price(t))
                             *t["sign"]*self._remaining(t) for t in self.state["trades"].values())
            if self.snapshot()["net"] + unrealised <= -self.max_daily_loss:
                self.state["flatten"] = True; self.journal.save()
            if self._budget_used() > self.daily_cap:
                self._halt("Actual fills exceed daily reservation; review execution prices")
            for t in list(self.state["trades"].values()): self._manage(t)
            # Refresh after any stop/exit action before considering another entry.
            self._refresh(); self._publish()
            for sig in pending:
                if self.ready: self._accept(sig)
            self._last_error = None
        except Exception as exc:
            self.ready = False
            message = str(exc)
            if message != self._last_error:
                self.alerts("investMITRA execution blocked: " + message)
                self._last_error = message
            logger.exception("Execution cycle failed; no blind retry of submissions")
        finally:
            self._publish()

    def run(self):
        while not self.stop_requested.is_set():
            self.step()
            self.stop_requested.wait(2)

    def mirror_to_neon(self):
        """Optional reporting mirror, separate from the execution-critical journal.

        This never adds P&L to the old paper summary. The dedicated table is
        account/mode/day scoped and replaces a full snapshot idempotently.
        """
        url = os.getenv("CC_POSTGRES_URL")
        if not url: return
        view = self.snapshot()
        if "day" not in view: return
        conn = None
        try:
            import psycopg2
            with psycopg2.connect(url, connect_timeout=5,
                                  options="-c statement_timeout=5000") as conn:
                with conn.cursor() as cur:
                    cur.execute("CREATE SCHEMA IF NOT EXISTS investmitra")
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS investmitra.execution_sessions (
                            account_id TEXT NOT NULL, execution_mode TEXT NOT NULL,
                            trade_date DATE NOT NULL, snapshot JSONB NOT NULL,
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            PRIMARY KEY (account_id, execution_mode, trade_date))
                    """)
                    cur.execute("""
                        INSERT INTO investmitra.execution_sessions
                            (account_id,execution_mode,trade_date,snapshot)
                        VALUES (%s,%s,%s,%s::jsonb)
                        ON CONFLICT (account_id,execution_mode,trade_date)
                        DO UPDATE SET snapshot=EXCLUDED.snapshot,updated_at=NOW()
                    """, (view["account"], view["mode"], view["day"], json.dumps(view, allow_nan=False)))
        except Exception:
            logger.warning("Neon execution mirror unavailable; local durable journal remains authoritative")
        finally:
            if conn is not None: conn.close()

    def mirror_loop(self):
        while not self.stop_requested.is_set():
            self.mirror_to_neon()
            self.stop_requested.wait(30)

    def report(self):
        v = self.snapshot()
        return (f"Execution {self.broker.mode}: tickets Rs{v.get('tickets',0):.2f}; "
                f"remaining after reservations Rs{v.get('remaining',0):.2f}; "
                f"gross Rs{v.get('gross',0):.2f}; provisional net Rs{v.get('net',0):.2f}; "
                f"flat={v.get('flat',False)}; {v.get('reason','')}")


def build_executor(kite, instruments, mode, **limits):
    if mode not in {"auto_paper", "live"}: raise ValueError("Unknown execution mode")
    account = os.getenv("KITE_USER_ID", "simulation")
    broker = KiteBroker(kite, account) if mode == "live" else None
    path = os.getenv("INVESTMITRA_EXECUTION_DB", f"data/execution_{mode}.sqlite3")
    journal = Journal(path, account, mode)
    try:
        if broker is None: broker = PaperBroker(kite, journal)
        return AutoOrderManager(broker, journal, instruments, **limits)
    except Exception:
        journal.close(); raise


if __name__ == "__main__":
    raise SystemExit("Start intraday_signals.py with INVESTMITRA_EXECUTION_MODE=auto_paper. Do not run a second order manager.")
