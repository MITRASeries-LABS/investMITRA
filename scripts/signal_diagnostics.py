"""Read-only admission evidence. No order submission or threshold tuning."""
import math
import re
from collections import Counter
from signal_evidence import sector_policy_rejection

VERSION = "decision-evidence-v1"


def direction_for(stock, in_long, in_short, market, gap, threshold, price, opening, vwap, fo):
    score = stock.get('investmitra_score', 50)
    quality = stock.get('quality_score', 50)
    if (in_long and market in ('BULLISH','NEUTRAL') and gap >= threshold and
            price >= opening*.998 and price > vwap*1.001 and
            score >= (55 if stock.get('market_cap_category') in ('MICRO','SMALL') else 60)):
        return 'LONG'
    if gap > -threshold or price > opening*1.002 or price >= vwap*.999 or not fo:
        return None
    if market == 'NEUTRAL' and score < 65:
        return None
    if in_short and stock.get('direction_override') != 'SHORT' and market in ('BEARISH','NEUTRAL') and score <= 40:
        return 'SHORT'
    if in_short and stock.get('direction_override') == 'SHORT' and market in ('BEARISH','NEUTRAL') and score >= 55:
        return 'SHORT'
    if in_long and market in ('BEARISH','NEUTRAL') and score >= (55 if market=='BEARISH' and quality>=60 else 65):
        return 'SHORT'
    return None


def reason_code(reason):
    # Numeric fluctuations must not defeat rejection throttling.
    return re.sub(r"[-+]?\d+(?:\.\d+)?", "#", reason)


def session_policy(session, gap_threshold, priority=3):
    return dict(session=session, gap_min=gap_threshold, blended_min=55,
                rvol_min=8 if session == "choppy" else 5,
                priority_min=max(priority, 5) if session == "choppy" else priority,
                opportunity_multiplier={"momentum": 1., "choppy": .7, "afternoon": .85}.get(session, .5))


def score_gates(signal, now, policy):
    """Evaluate independent gates even if admission returned at its first failure.

    A pass is only a pass for this gate. Missing inputs remain UNKNOWN. Portfolio
    sizing and execution freshness must still pass their authoritative checks.
    """
    gates = {}
    def check(name, value, predicate, required=None):
        try:
            number = float(value)
            valid = math.isfinite(number)
        except (TypeError, ValueError, OverflowError):
            valid = False
        gates[name] = dict(status=("PASS" if predicate(number) else "FAIL") if valid else "UNKNOWN",
                           actual=number if valid else None, required=required)
    details = signal.get("details", {})
    check("gap", signal.get("true_gap"), lambda x: abs(x) >= policy['gap_min'], policy['gap_min'])
    check("blended", signal.get("final_score"), lambda x: x >= policy['blended_min'], policy['blended_min'])
    check("rvol", details.get("rvol_uncapped", details.get("rvol")), lambda x: x >= policy['rvol_min'], policy['rvol_min'])
    try:
        priority = round(float(details['rvol'])*abs(float(signal['true_gap']))*float(signal['final_score'])/100, 3)
    except (KeyError, TypeError, ValueError):
        priority = None
    check("priority", priority, lambda x: x >= policy['priority_min'], policy['priority_min'])
    typ = details.get('gap_type')
    gates['gap_type'] = dict(status='UNKNOWN' if typ is None else 'PASS' if typ in
        {'continuation', 'continuation_strong', 'fade_risk'} else 'FAIL', actual=typ)
    sector = sector_policy_rejection(signal, now)
    gates['sector'] = dict(status='PASS' if not sector else 'UNKNOWN' if details.get('sector_status') != 'fresh'
                           else 'FAIL', reason=sector, actual=details.get('sector_chg'))
    check('atr', signal.get('atr'), lambda x: x > 0, '>0')
    for name in ('direction_vwap', 'sizing_profit', 'portfolio_risk', 'execution_admission'):
        gates[name] = dict(status='NOT_EVALUATED')
    return gates


def decision_record(engine, symbol, price, volume, now, session, reason):
    scored = engine._shadow_scored or {}
    details = scored.get('details', {})
    stock = engine.all_stocks.get(symbol, {})
    gap = scored.get('gap')
    weights = dict(engine.signal_weights)
    policy = session_policy(session, getattr(engine, '_diagnostic_gap_threshold', .3))
    signal = dict(true_gap=gap, final_score=scored.get('blended'), details=details,
                  direction='SHORT' if gap is not None and gap < 0 else 'LONG',
                  signal_weights=weights, atr=engine.key_levels.get(symbol, {}).get('atr14'))
    gates = score_gates(signal, now, policy) if scored else {}
    if scored:
        direction = direction_for(stock, symbol in engine.long_map, symbol in engine.short_map,
            engine.market_direction, gap, policy['gap_min'], price, engine.today_open.get(symbol,price),
            engine.vwap.get(symbol,price), engine._is_fo_eligible(symbol))
        gates['direction_vwap'] = dict(status='PASS' if direction else 'FAIL', actual=direction)
        for name, passed in getattr(engine, '_decision_checks', {}).items():
            gates[name] = dict(status='PASS' if passed else 'FAIL')
        if reason == 'queued_not_filled':
            gates['execution_admission'] = dict(status='QUEUED_NOT_FILLED')
    return dict(version=VERSION, day=now.date().isoformat(), strategy_id=engine.strategy_id,
        symbol=symbol, observed_at=now.timestamp(), session=session, price=price, volume=volume,
        outcome=reason, reason_code=reason_code(reason), policy=policy, gates=gates,
        quality=scored.get('quality'), opportunity=scored.get('opportunity'), blended=scored.get('blended'),
        quality_version=stock.get('quality_version', 'unversioned'), details=details,
        authoritative_stage=getattr(engine, '_decision_stage', 'pre_score'),
        sizing=getattr(engine, '_decision_sizing', None),
        scope='independent score gates plus authoritative terminal stage; not a counterfactual fill')


def summarise_decisions(records):
    """First scored observation per symbol/session, or first pre-score observation if none reached scoring."""
    unique = {}
    for row in sorted(records, key=lambda r: r['observed_at']):
        key = row['day'], row['strategy_id'], row['symbol'], row['session']
        if key not in unique or (not unique[key]['gates'] and row['gates']):
            unique[key] = row
    stages = Counter(r['reason_code'] for r in unique.values())
    blockers, unknown = Counter(), Counter()
    for row in unique.values():
        blockers.update(k for k,v in row['gates'].items() if v['status']=='FAIL')
        unknown.update(k for k,v in row['gates'].items() if v['status']=='UNKNOWN')
    return dict(symbol_sessions=len(unique), failures=dict(blockers), unknown=dict(unknown),
                outcomes=dict(stages))
