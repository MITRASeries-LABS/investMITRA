"""Read-only daily report from the auto_paper SQLite journal.

Run: python scripts/auto_paper_summary.py [--date YYYY-MM-DD]
Budget defaults match the uploaded executor. If its settings change, supply
--daily-cap and --cost-reserve; these report options do not change execution.
Signal cost estimates are not actual broker charges. Open-trade net includes
the full estimated trade cost and excludes unrealised price movements.
"""
import argparse
import json
import os
import sqlite3
from decimal import Decimal
from pathlib import Path

TERMINAL = {"COMPLETE", "CANCELLED", "REJECTED"}
ZERO = Decimal("0")


def number(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Non-finite value in journal or report settings")
    return result


def load_state(db_path, target_date=None):
    """Use the actual Journal schema; never create or modify the database."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        row = conn.execute("SELECT body FROM state WHERE id=1").fetchone()
        state = json.loads(row[0]) if row else None
        if target_date and (state is None or state.get("day") != target_date):
            row = conn.execute(
                "SELECT body FROM history WHERE day=?", (target_date,)
            ).fetchone()
            state = json.loads(row[0]) if row else None
        return state
    finally:
        conn.close()


def calculate(state, daily_cap=25000, cost_reserve=80):
    """Derive usage from fills, including closed trades, as the executor does."""
    cap, reserve = number(daily_cap), number(cost_reserve)
    if cap <= 0 or reserve < 0:
        raise ValueError("Daily cap must be positive; cost reserve cannot be negative")
    totals = dict(tickets=ZERO, pending=ZERO, allowances=ZERO, exposure=ZERO,
                  gross=ZERO, estimated_costs=ZERO, executor_costs=ZERO,
                  wins=0, losses=0, breakeven=0, rows=[])
    for symbol, trade in state.get("trades", {}).items():
        orders = trade.get("orders", [])
        entries = [o for o in orders if o["kind"] == "ENTRY"]
        if len(entries) != 1:
            raise ValueError(f"{symbol}: expected one entry order in executor journal")
        entry = entries[0]
        qty = number(entry.get("filled", 0))
        ordered = number(entry["qty"])
        average = number(entry.get("average", 0))
        if qty < 0 or ordered < qty or (qty > 0 and average <= 0):
            raise ValueError(f"{symbol}: invalid entry fill")
        exits = [o for o in orders if o["kind"] in ("EXIT", "STOP")]
        exited = ZERO
        exit_value = ZERO
        for order in exits:
            filled, price = number(order.get("filled", 0)), number(order.get("average", 0))
            if filled < 0 or (filled > 0 and price <= 0):
                raise ValueError(f"{symbol}: invalid exit fill")
            exited += filled
            exit_value += filled * price
        if exited > qty:
            raise ValueError(f"{symbol}: exits exceed entry fills")
        remaining = qty - exited
        if trade.get("closed_at") and remaining:
            raise ValueError(f"{symbol}: marked closed with unexited shares")
        sign = number(trade["sign"])
        if sign not in (1, -1):
            raise ValueError(f"{symbol}: invalid direction")
        entry_value = qty * average
        gross = (exit_value - average * exited) * sign
        pending = ZERO
        active_entry = entry["status"] not in TERMINAL
        if active_entry:
            reservation = number(trade["reservation_price"])
            if reservation <= 0:
                raise ValueError(f"{symbol}: invalid reservation price")
            pending = (ordered - qty) * reservation
        allowance = reserve if active_entry or qty > 0 else ZERO
        costs = number(trade.get("signal", {}).get("estimated_costs", reserve)) if qty else ZERO
        if costs < 0:
            raise ValueError(f"{symbol}: negative estimated costs")
        closed = bool(trade.get("closed_at")) and qty > 0
        status = (trade.get("exit_reason") or "CLOSED") if closed else (
            "NOT FILLED" if not qty else "PARTIAL / OPEN" if exited else "OPEN")
        net = gross - costs
        if closed:
            totals["wins" if net > 0 else "losses" if net < 0 else "breakeven"] += 1
        for key, value in dict(tickets=entry_value, pending=pending,
                               allowances=allowance, exposure=remaining * average,
                               gross=gross, estimated_costs=costs,
                               executor_costs=reserve if qty else ZERO).items():
            totals[key] += value
        totals["rows"].append(dict(symbol=trade.get("symbol", symbol),
                                   direction="LONG" if sign > 0 else "SHORT",
                                   qty=qty, exited=exited, entry=average,
                                   exit=exit_value / exited if exited else ZERO,
                                   gross=gross, net=net, status=status))
    totals["budget_used"] = totals["tickets"] + totals["pending"] + totals["allowances"]
    totals["remaining"] = max(ZERO, cap - totals["budget_used"])
    totals["net"] = totals["gross"] - totals["estimated_costs"]
    totals["executor_net"] = totals["gross"] - totals["executor_costs"]
    return totals


def summarise(db_path="data/execution_auto_paper.sqlite3", target_date=None,
              daily_cap=25000, cost_reserve=80):
    if not Path(db_path).is_file():
        print("Journal not found:", db_path)
        return
    state = load_state(db_path, target_date)
    if state is None:
        print(f"No data for {target_date}" if target_date else "No state in journal.")
        return
    result = calculate(state, daily_cap, cost_reserve)
    print(f"\n{'='*80}\n  AUTO-PAPER SUMMARY — {state.get('day', '?')}")
    print(f"  Mode: {state.get('mode', '?')} | Account: {state.get('account', '?')}")
    print(f"{'='*80}")
    print(f"\n  {'Symbol':<12} {'Dir':<5} {'Qty':>4} {'Exited':>6} {'Entry':>9} {'Exit':>9} {'Gross':>9} {'Est. net':>9}  Status")
    for row in result["rows"]:
        exit_text = f"{row['exit']:.2f}" if row["exited"] else "--"
        print(f"  {row['symbol']:<12} {row['direction']:<5} {row['qty']:>4.0f} "
              f"{row['exited']:>6.0f} {row['entry']:>9.2f} {exit_text:>9} "
              f"{row['gross']:>+9.2f} {row['net']:>+9.2f}  {row['status']}")
    print(f"\n  Realised gross: ₹{result['gross']:+,.2f}")
    print(f"  Signal-estimated costs: ₹{result['estimated_costs']:,.2f} | Estimated net: ₹{result['net']:+,.2f}")
    print(f"  Executor provisional net (₹{number(cost_reserve):,.2f}/filled trade): ₹{result['executor_net']:+,.2f}")
    completed = result["wins"] + result["losses"] + result["breakeven"]
    if completed:
        print(f"  Closed trades: {completed} | Wins: {result['wins']} | Losses: {result['losses']} | Breakeven: {result['breakeven']} | Win rate: {result['wins']/completed*100:.0f}%")
    else:
        print("  No completed trades.")
    print(f"\n  Daily entry allocation (closed trades retained): ₹{result['tickets']:,.2f}")
    print(f"  Pending entry reservation: ₹{result['pending']:,.2f}")
    print(f"  Executor cost allowances: ₹{result['allowances']:,.2f}")
    print(f"  Budget used incl. reservations: ₹{result['budget_used']:,.2f} of ₹{number(daily_cap):,.2f}")
    print(f"  Remaining daily allowance: ₹{result['remaining']:,.2f}")
    print(f"  Open exposure at entry prices: ₹{result['exposure']:,.2f}")
    if result["budget_used"] > number(daily_cap):
        print("  WARNING: journal usage exceeds the configured report cap.")
    print("  Costs are estimates, not confirmed broker charges; unrealised P&L excluded.")
    print("  Budget reconstruction assumes --daily-cap and --cost-reserve match the executor.")
    print(f"  Halt: {state.get('halt') or 'none'}\n{'='*80}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None)
    parser.add_argument("--db", default=os.getenv("INVESTMITRA_EXECUTION_DB", "data/execution_auto_paper.sqlite3"))
    parser.add_argument("--daily-cap", type=Decimal, default=Decimal("25000"))
    parser.add_argument("--cost-reserve", type=Decimal, default=Decimal("80"))
    args = parser.parse_args()
    try:
        summarise(args.db, args.date, args.daily_cap, args.cost_reserve)
    except (sqlite3.Error, ValueError, KeyError, TypeError, ArithmeticError) as error:
        parser.exit(1, f"Cannot produce a reliable summary: {error}\n")
