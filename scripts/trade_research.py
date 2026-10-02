"""Bounded, observation-only trade paths. No broker, database or notification I/O.

Excursions are sampled price movements per share relative to entry VWAP, not
portfolio P&L. Partial fills/exits are not turned into hypothetical full-size P&L.
Post-exit marks use first fresh quotes in a 60-second window, never interpolation.
"""
import math
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
VERSION = "trade-path-v1"
HORIZONS = (15, 30, 60)
GRACE_SECONDS = 60


def timestamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        raise ValueError("missing timestamp")
    return value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)


def fresh_price(quote, now):
    try:
        at = timestamp(quote.get("timestamp"))
        price = float(quote.get("last_price"))
        if math.isfinite(price) and price > 0 and 0 <= (now-at).total_seconds() <= 10 and at.date() == now.date():
            return price, at
    except (ValueError, TypeError, AttributeError, OverflowError):
        pass
    return None, None


def observe_trade(trade, quote, now):
    entries = [o for o in trade["orders"] if o["kind"] == "ENTRY" and o["filled"]]
    qty = sum(o["filled"] for o in entries)
    if not qty:
        return  # an intent or rejected/unfilled order is not a trade observation
    entry = sum(o["filled"] * o["average"] for o in entries) / qty
    sign = trade["sign"]
    closed = timestamp(trade["closed_at"]) if trade.get("closed_at") else None
    started = timestamp(trade.get("first_fill_observed_at") or trade.get("entry_at") or now)
    r = trade.setdefault("research", dict(version=VERSION, started_at=now.isoformat(),
        first_fill_observed_at=started.isoformat(), sampled_quotes=0, first_quote_at=None,
        last_quote_at=None, high=None, low=None, max_gap_seconds=0, post_exit={}))
    r.update(entry_vwap=entry, filled_qty=qty, sign=sign,
             basis="sampled_price_per_share_from_entry_vwap_not_portfolio_pnl")
    price, at = fresh_price(quote, now)
    # Includes a quote observed at closure, but never a later post-exit price.
    if at is not None and at >= started and (closed is None or at <= closed):
        last = timestamp(r["last_quote_at"]) if r["last_quote_at"] else None
        if last is None or at > last:
            r["max_gap_seconds"] = max(r["max_gap_seconds"], (at-(last or started)).total_seconds())
            r["first_quote_at"] = r["first_quote_at"] or at.isoformat()
            r["last_quote_at"] = at.isoformat()
            r["sampled_quotes"] += 1
            r["high"] = max(r["high"], price) if r["high"] is not None else price
            r["low"] = min(r["low"], price) if r["low"] is not None else price
    coverage_end = closed or now
    r["max_gap_seconds"] = max(r["max_gap_seconds"], max(0, (coverage_end-timestamp(r["last_quote_at"] or started)).total_seconds()))
    r["coverage"] = "no_fresh_quotes" if not r["sampled_quotes"] else "gapped" if r["max_gap_seconds"] > 10 else "sampled"
    if r["sampled_quotes"]:
        r["mfe_per_share"] = max(0, (r["high"]-entry)*sign, (r["low"]-entry)*sign)
        r["mae_per_share"] = min(0, (r["high"]-entry)*sign, (r["low"]-entry)*sign)
        risk = trade.get("initial_risk")
        r["mfe_r"] = r["mfe_per_share"]/risk if risk and risk > 0 else None
        r["mae_r"] = r["mae_per_share"]/risk if risk and risk > 0 else None
    if closed is None:
        return
    exits = [o for o in trade["orders"] if o["kind"] in {"EXIT", "STOP"} and o["filled"]]
    exited = sum(o["filled"] for o in exits)
    if exited != qty:
        raise ValueError("closed trade quantities do not reconcile")
    exit_vwap = sum(o["filled"]*o["average"] for o in exits) / exited
    r.update(closed_at=closed.isoformat(), exit_vwap=exit_vwap,
             close_time_basis="executor_fill_recognition", observation_end_at=closed.replace(hour=15, minute=5, second=0, microsecond=0).isoformat())
    session_end = timestamp(r["observation_end_at"])
    for minutes in HORIZONS:
        due = closed + timedelta(minutes=minutes)
        mark = r["post_exit"].setdefault(str(minutes), dict(due_at=due.isoformat(), status="PENDING"))
        if mark["status"] == "CAPTURE_STOPPED":
            mark["status"] = "PENDING"  # same-day restart can resume future horizons
        if mark["status"] != "PENDING":
            continue  # idempotent across restarts and repeated quotes
        if due > session_end:
            mark["status"] = "CENSORED_SESSION_END"
        elif at is not None and due <= at <= min(due + timedelta(seconds=GRACE_SECONDS), session_end):
            mark.update(status="COMPLETE", price=price, quote_at=at.isoformat(),
                delay_seconds=(at-due).total_seconds(),
                move_from_entry_per_share=(price-entry)*sign,
                move_from_exit_vwap_per_share=(price-exit_vwap)*sign)
        elif now > due + timedelta(seconds=GRACE_SECONDS):
            mark["status"] = "MISSING_FRESH_QUOTE"
        elif now > session_end:
            mark["status"] = "CENSORED_SESSION_END"


def finish_observation(trades, now):
    """Call only after the worker stops. Pending horizons remain explicitly unknown."""
    for trade in trades.values():
        r = trade.get("research")
        if not r:
            continue
        r["capture_stopped_at"] = now.isoformat()
        for mark in r["post_exit"].values():
            if mark["status"] == "PENDING":
                mark["status"] = "CAPTURE_STOPPED"


def print_research(state):
    trades = state.get("trades", {})
    observed = [(s, t) for s, t in trades.items() if t.get("research")]
    if not observed:
        return
    print("\n  TRADE PATH RESEARCH — sampled prices, not realised or alternative-strategy P&L")
    print("  MFE/MAE and post-exit moves are direction-aware Rs/share; costs excluded.")
    print("  Horizons: first fresh quote at/after due time, within 60 seconds; capture ends by 15:05.")
    for symbol, t in observed:
        r = t["research"]
        mfe, mae = r.get("mfe_per_share"), r.get("mae_per_share")
        excursion = f"MFE {mfe:+.2f}, MAE {mae:+.2f}" if mfe is not None else "MFE/MAE unavailable"
        print(f"  {symbol}: {excursion}; {r['sampled_quotes']} quotes; coverage={r['coverage']}; max gap={r['max_gap_seconds']:.0f}s")
        for minutes, mark in r["post_exit"].items():
            if mark["status"] == "COMPLETE":
                print(f"    +{minutes}m price {mark['price']:.2f}; vs entry {mark['move_from_entry_per_share']:+.2f}; vs exit VWAP {mark['move_from_exit_vwap_per_share']:+.2f}")
            else:
                print(f"    +{minutes}m {mark['status']}")
        if t.get("research_error"):
            print("    WARNING: research capture encountered errors; coverage incomplete")
    print("  No missing price is treated as a loss. Sampled extrema can miss moves between quotes.")
    print("  Post-exit marks ignore alternate stops, partial exits and capital competition.\n")
