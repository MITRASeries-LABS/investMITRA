"""Pure accounting shared by execution and reports; no broker or database I/O."""
from decimal import Decimal

REUSABLE = "reusable_equity_v1"
LEGACY = "cumulative_tickets_v1"
TERMINAL = {"COMPLETE", "CANCELLED", "REJECTED"}
ZERO = Decimal("0")


def amount(value):
    value = Decimal(str(value))
    if not value.is_finite():
        raise ValueError("Non-finite execution amount")
    return value


def capital_snapshot(state, cap, cost_reserve):
    """Release exposure only from confirmed exit fills, never from exit intent.

    Profits can offset realised losses/costs but cannot increase the capital ceiling.
    Unknown/submitted entry quantities remain reserved at the order's bound.
    Missing model denotes a historical cumulative-budget journal, not a migration.
    """
    cap, reserve = amount(cap), amount(cost_reserve)
    model = state.get("capital_model", LEGACY)
    if model not in {LEGACY, REUSABLE} or cap <= 0 or reserve < 0:
        raise ValueError("Invalid capital model or limits")
    result = dict(tickets=ZERO, exposure=ZERO, pending=ZERO, allowances=ZERO,
                  executor_costs=ZERO, gross=ZERO, pending_costs=ZERO)
    for t in state.get("trades", {}).values():
        orders = t["orders"]
        e = orders[0]
        qty, filled, price = map(amount, (e["qty"], e["filled"], e["average"]))
        if e["kind"] != "ENTRY" or not 0 <= filled <= qty or (filled and price <= 0):
            raise ValueError("Invalid entry fill")
        exited = ZERO
        proceeds = ZERO
        for o in orders[1:]:
            n, p = amount(o["filled"]), amount(o["average"])
            if o["kind"] not in {"STOP", "EXIT"} or not 0 <= n <= amount(o["qty"]) or (n and p <= 0):
                raise ValueError("Invalid exit fill")
            exited += n
            proceeds += n * p
        sign = amount(t["sign"])
        if exited > filled or sign not in (-1, 1):
            raise ValueError("Invalid position quantity/direction")
        active = e["status"] not in TERMINAL
        if active:
            bound = amount(t["reservation_price"])
            if bound <= 0:
                raise ValueError("Invalid pending entry bound")
            result["pending"] += (qty - filled) * bound
        result["tickets"] += filled * price  # turnover, NOT current exposure
        result["exposure"] += (filled - exited) * price
        result["gross"] += (proceeds - exited * price) * sign
        result["allowances"] += reserve if filled or active else ZERO
        result["executor_costs"] += reserve if filled else ZERO
        result["pending_costs"] += reserve if active and not filled else ZERO
    result["executor_net"] = result["gross"] - result["executor_costs"]
    result["capital_reduction"] = max(ZERO, -result["executor_net"])
    result["equity"] = max(ZERO, cap - result["capital_reduction"])
    result["budget_used"] = (
        result["tickets"] + result["pending"] + result["allowances"] if model == LEGACY
        else result["exposure"] + result["pending"] + result["pending_costs"] + result["capital_reduction"])
    result["remaining"] = max(ZERO, cap - result["budget_used"])
    result["capital_model"] = model
    return result
