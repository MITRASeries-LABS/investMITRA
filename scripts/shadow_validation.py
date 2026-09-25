"""Forward-only candidate research. No broker, trading journal or network access.

This measures fixed 30-minute price markouts, NOT an execution backtest. One
first scored rejection and one first queued candidate per symbol/direction/day
are separate cohorts. Candidates before the scoring stage are outside coverage.
"""
from datetime import datetime, timedelta, timezone
import json
import logging
import math
from pathlib import Path
import queue
import sqlite3
import threading

IST = timezone(timedelta(hours=5, minutes=30))
VERSION = "markout30-v1"
SPEC = {"horizon_seconds": 1800, "exit_grace_seconds": 60,
        "max_price_age_seconds": 60, "notional": 10000,
        "min_ticket": 1000, "cost_allowance": 80,
        "slippage_bps_per_side": 5, "stress_cost_allowance": 160,
        "stress_slippage_bps_per_side": 10, "max_vwap_extension_atr": 1.0}
LOG = logging.getLogger(__name__)


def finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def timestamp(value):
    try:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if not isinstance(value, datetime):
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=IST)
        return value.timestamp()
    except (ValueError, OverflowError):
        return None


def price_fresh(observed_at, price_at):
    age = observed_at - price_at if price_at is not None else None
    return age is not None and 0 <= age <= SPEC["max_price_age_seconds"]


def filters(features):
    """None means untestable, never a fabricated pass or rejection."""
    sign = 1 if features["direction"] == "LONG" else -1
    price, atr, vwap = (finite(features.get(k)) for k in ("entry", "atr", "vwap"))
    extension = None
    if price and atr and atr > 0 and vwap and features.get("vwap_source") == "exchange_atp":
        extension = 0 <= sign * (price - vwap) / atr <= SPEC["max_vwap_extension_atr"]
    hi, lo = finite(features.get("or_high")), finite(features.get("or_low"))
    breakout = None
    if features.get("opening_range_complete") and hi and lo and hi > lo and price:
        breakout = price > hi if sign > 0 else price < lo
    both = None if extension is None or breakout is None else extension and breakout
    return {"vwap_extension": extension, "opening_breakout": breakout, "both": both}


class StudyStore:
    """SQLite used only on the observer thread (or directly by offline tests)."""
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=2)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS study_specs(version TEXT PRIMARY KEY, spec TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS observations(
            id TEXT PRIMARY KEY, version TEXT NOT NULL, strategy_id TEXT NOT NULL,
            day TEXT NOT NULL, symbol TEXT NOT NULL, direction TEXT NOT NULL,
            cohort TEXT NOT NULL, observed_at REAL NOT NULL, due_at REAL NOT NULL,
            entry REAL NOT NULL, qty INTEGER NOT NULL, features TEXT NOT NULL,
            filters TEXT NOT NULL, status TEXT NOT NULL,
            exit_at REAL, exit_price REAL, gross REAL, net REAL, stress_net REAL);
          CREATE TABLE IF NOT EXISTS observer_health(
            run_id TEXT PRIMARY KEY, updated_at REAL NOT NULL,
            dropped_events INTEGER NOT NULL, error TEXT);
          CREATE INDEX IF NOT EXISTS shadow_pending ON observations(symbol,status,due_at);
        """)
        spec = json.dumps(SPEC, sort_keys=True)
        self.db.execute("INSERT OR IGNORE INTO study_specs VALUES (?,?)", (VERSION, spec))
        if self.db.execute("SELECT spec FROM study_specs WHERE version=?", (VERSION,)).fetchone()[0] != spec:
            self.db.close()
            raise ValueError("Shadow study specification changed without a new version")
        self.db.commit()

    @staticmethod
    def key(features):
        return "|".join((VERSION, features["day"], features["strategy_id"],
                         features["symbol"], features["direction"], features["cohort"]))

    def candidate(self, features):
        price, at = finite(features.get("entry")), finite(features.get("observed_at"))
        if not price or price <= 0 or at is None or features.get("direction") not in {"LONG", "SHORT"}:
            return
        local = datetime.fromtimestamp(at, IST)
        if not 575 <= local.hour * 60 + local.minute < 870:  # 09:35 <= entry < 14:30
            return
        qty = int(SPEC["notional"] // price)
        status = "PENDING"
        if not price_fresh(at, features.get("price_at")):
            status = "ENTRY_PRICE_UNVERIFIED"
        elif qty * price < SPEC["min_ticket"] or qty == 0:
            status = "UNSIZABLE"
        self.db.execute("""INSERT OR IGNORE INTO observations
          (id,version,strategy_id,day,symbol,direction,cohort,observed_at,due_at,
           entry,qty,features,filters,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (self.key(features), VERSION, features["strategy_id"], features["day"],
           features["symbol"], features["direction"], features["cohort"], at,
           at + SPEC["horizon_seconds"], price, qty,
           json.dumps(features, sort_keys=True, allow_nan=False),
           json.dumps(filters(features)), status))

    def quote(self, symbol, price, at, price_at):
        price = finite(price)
        if not price or price <= 0 or not price_fresh(at, price_at):
            return
        rows = self.db.execute("""SELECT id,direction,entry,qty,due_at FROM observations
            WHERE symbol=? AND status='PENDING' AND due_at<=? AND due_at>=?""",
            (symbol, at, at - SPEC["exit_grace_seconds"])).fetchall()
        for key, direction, entry, qty, due in rows:
            if price_at < due:  # don't use a stale last trade preceding the horizon
                continue
            sign = 1 if direction == "LONG" else -1
            gross = sign * (price - entry) * qty
            turnover = (price + entry) * qty
            net = gross - SPEC["cost_allowance"] - turnover * SPEC["slippage_bps_per_side"] / 10000
            stress = gross - SPEC["stress_cost_allowance"] - turnover * SPEC["stress_slippage_bps_per_side"] / 10000
            self.db.execute("""UPDATE observations SET status='COMPLETE',exit_at=?,exit_price=?,
                gross=?,net=?,stress_net=? WHERE id=? AND status='PENDING'""",
                (at, price, gross, net, stress, key))

    def expire(self, at):
        self.db.execute("""UPDATE observations SET status='MISSING_EXIT'
            WHERE status='PENDING' AND due_at + ? < ?""", (SPEC["exit_grace_seconds"], at))

    def close(self):
        self.db.commit()
        self.db.close()


class ShadowObserver:
    """Bounded, nonblocking producer; all disk work runs on a separate thread."""
    def __init__(self, path, queue_size=4096):
        self.path = str(path)
        self.events = queue.Queue(maxsize=queue_size)
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.failed = None
        self.dropped = 0
        self.seen = set()
        self.run_id = datetime.now(IST).isoformat()
        self.worker = threading.Thread(target=self._run, name="shadow-observer", daemon=True)
        self.worker.start()
        if not self.ready.wait(5) or self.failed:
            self.stop.set()
            raise RuntimeError(self.failed or "shadow observer initialization timed out")

    def _put(self, event):
        if self.failed or self.stop.is_set():
            return False
        try:
            self.events.put_nowait(event)
            return True
        except queue.Full:
            self.dropped += 1
            if self.dropped == 1 or self.dropped % 1000 == 0:
                LOG.warning("Shadow research queue full: %d events lost; trading continues", self.dropped)
            return False

    def candidate(self, features):
        key = StudyStore.key(features)
        # Snapshot before queueing; caller-owned dictionaries may subsequently change.
        if key not in self.seen and self._put(("candidate", json.loads(json.dumps(features, allow_nan=False)))):
            self.seen.add(key)

    def quote(self, symbol, price, at, price_at):
        self._put(("quote", symbol, price, at, price_at))

    def _run(self):
        store = None
        try:
            store = StudyStore(self.path)
            self.seen = {r[0] for r in store.db.execute("SELECT id FROM observations")}
            self.ready.set()
            last_commit = datetime.now(IST).timestamp()
            while not self.stop.is_set() or not self.events.empty():
                try:
                    event = self.events.get(timeout=0.5)
                except queue.Empty:
                    event = None
                if event:
                    if event[0] == "candidate":
                        store.candidate(event[1])
                    else:
                        store.quote(*event[1:])
                now = datetime.now(IST).timestamp()
                if now - last_commit >= 1 or event is None:
                    # Expire after draining queued observations, avoiding backlog-induced bias.
                    if self.events.empty():
                        store.expire(now)
                    store.db.execute("INSERT OR REPLACE INTO observer_health VALUES (?,?,?,?)",
                                     (self.run_id, now, self.dropped, None))
                    store.db.commit()
                    last_commit = now
            store.db.execute("INSERT OR REPLACE INTO observer_health VALUES (?,?,?,?)",
                             (self.run_id, datetime.now(IST).timestamp(), self.dropped, None))
        except Exception as exc:
            self.failed = str(exc)
            LOG.exception("Shadow observer failed; research incomplete; trading unchanged")
        finally:
            self.ready.set()
            if store:
                try:
                    if self.failed:
                        store.db.rollback()
                        store.db.execute("INSERT OR REPLACE INTO observer_health VALUES (?,?,?,?)",
                            (self.run_id, datetime.now(IST).timestamp(), self.dropped, self.failed))
                    store.close()
                except Exception:
                    LOG.exception("Cannot persist shadow observer health")

    def close(self):
        self.stop.set()
        self.worker.join(timeout=5)
        if self.worker.is_alive():
            LOG.warning("Shadow writer still draining; last research records may be incomplete")


def capture_features(engine, symbol, price, now, session, scored, tick, queued):
    """Copy current data. Never refresh inputs, call a broker or mutate signal state."""
    stock = engine.all_stocks[symbol]
    atr = engine.key_levels.get(symbol, {}).get("atr14")
    gap = scored["gap"]
    hi, lo = finite(engine.or_high.get(symbol)), finite(engine.or_low.get(symbol))
    span = engine._shadow_opening.get(symbol, {})
    complete = bool(span and span["first"] <= 556 and span["last"] >= 569 and span["max_gap"] <= 60)
    return {"day": now.date().isoformat(), "observed_at": now.timestamp(),
        "price_at": timestamp(tick.get("last_trade_time")),
        "strategy_id": engine.strategy_id, "symbol": symbol, "entry": price,
        "direction": "LONG" if gap > 0 else "SHORT", "cohort": "QUEUED" if queued else "SCORED_REJECTED",
        "reason": "queued_not_filled" if queued else scored.get("reason", "later_strategy_or_execution_gate"),
        "session": session, "market_direction": engine.market_direction,
        "gap_pct": gap, "quality": scored["quality"], "opportunity": scored["opportunity"],
        "blended": scored["blended"], "rvol": finite(scored["details"].get("rvol")),
        "details": dict(scored["details"]), "atr": finite(atr),
        "vwap": finite(engine.vwap.get(symbol)),
        "vwap_source": "exchange_atp" if tick.get("average_traded_price", 0) > 0 else "approximate_or_unknown",
        "or_high": hi, "or_low": lo, "opening_range_complete": complete,
        "today_open": engine.today_open.get(symbol), "previous_close": engine.prev_close.get(symbol),
        "volume": tick.get("volume_traded"), "cap": stock.get("cap", stock.get("market_cap_category", "?")),
        "sector": stock.get("sector"), "breadth_startup_only": dict(engine.breadth.get("NIFTY 50", {})),
        "breadth_at_entry_verified": False}
