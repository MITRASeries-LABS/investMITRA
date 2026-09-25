"""Read-only, paired candidate filter comparisons. Not realised trading P&L."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
import sqlite3

from shadow_validation import IST, SPEC, VERSION


def metrics(rows, field="net"):
    values = [r[field] for r in sorted(rows, key=lambda r: (r["exit_at"], r["id"]))]
    wins = sum(v > 0 for v in values)
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {"n": len(values), "wins": wins, "win_rate": wins / len(values) if values else None,
            "mean_net": sum(values) / len(values) if values else None,
            "sum_net": sum(values), "sample_drawdown": drawdown}


def compare(rows, field="net"):
    complete = [r for r in rows if r["status"] == "COMPLETE"]
    result = {"all": metrics(complete, field), "filters": {}}
    for name in ("vwap_extension", "opening_breakout", "both"):
        known = [r for r in complete if r["filters"].get(name) is not None]
        kept = [r for r in known if r["filters"][name]]
        removed = [r for r in known if not r["filters"][name]]
        result["filters"][name] = {
            "reference": metrics(known, field), "kept": metrics(kept, field),
            "unknown": len(complete) - len(known),
            "losers_removed": sum(r[field] < 0 for r in removed),
            "winners_removed": sum(r[field] > 0 for r in removed),
            "removed_net": sum(r[field] for r in removed)}
    return result


def read_report(path, start, end):
    # mode=ro prevents accidental creation/reset of a missing journal.
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in db.execute(
            "SELECT * FROM observations WHERE day>=? AND day<=? ORDER BY observed_at,id", (start, end))]
        health = [dict(r) for r in db.execute(
            "SELECT * FROM observer_health WHERE updated_at>=? AND updated_at<?",
            (datetime.fromisoformat(start).replace(tzinfo=IST).timestamp(),
             datetime.fromisoformat(end).replace(tzinfo=IST).timestamp() + 86400))]
        specs = dict(db.execute("SELECT version,spec FROM study_specs"))
    finally:
        db.close()
    groups = defaultdict(list)
    for row in rows:
        row["filters"] = json.loads(row["filters"])
        # Report unknown versions explicitly; never silently pool changed experiments.
        groups[(row["version"], row["strategy_id"], row["cohort"])].append(row)
    return groups, health, specs


def summarise(path, start, end):
    groups, health, specs = read_report(path, start, end)
    print(f"SHADOW CANDIDATE STUDY {start} to {end}")
    print("30-minute markouts; queued != filled. These are NOT realised P&L or strategy win rates.")
    print("First scored rejection and first queued observation per symbol/direction/day are separate cohorts.")
    print("Pre-score rejects, unmonitored stocks and entry-blocked periods are outside coverage.")
    print("Paper fills, partial exits, stops, portfolio/risk limits and capital competition are not simulated.")
    print(f"Writer dropped events: {sum(r['dropped_events'] for r in health)}; errors: {sum(bool(r['error']) for r in health)}")
    if any(r['dropped_events'] or r['error'] for r in health):
        print("INCOMPLETE CAPTURE: gaps can bias this comparison; inspect engine logs too.")
    if not groups:
        print("No observations in this date range. No historical outcomes have been invented.")
    for (version, strategy, cohort), rows in sorted(groups.items()):
        print(f"\n{cohort} | {version} | strategy {strategy}")
        print("Coverage:", dict(Counter(r["status"] for r in rows)))
        print("Frozen assumptions:", specs.get(version, "MISSING SPECIFICATION"))
        if version != VERSION or json.loads(specs.get(version, "{}")) != SPEC:
            print("Unsupported specification; use matching report version. Comparison skipped.")
            continue
        for field, label in (("net", "BASE COSTS"), ("stress_net", "STRESS COSTS")):
            result = compare(rows, field)
            base = result["all"]
            print(f"  {label}: complete={base['n']} sessions={len({r['day'] for r in rows if r['status']=='COMPLETE'})}")
            print("  Filter             Ref N  Keep N  Ref win  Keep win  Ref mean  Keep mean  Missed wins  Avoided losses  Unknown")
            for name, data in result["filters"].items():
                ref, kept = data["reference"], data["kept"]
                pct = lambda v: '--' if v is None else f'{v*100:.1f}%'
                money = lambda v: '--' if v is None else f'{v:+.2f}'
                print(f"  {name:18} {ref['n']:5} {kept['n']:7} {pct(ref['win_rate']):>8} {pct(kept['win_rate']):>9} "
                      f"{money(ref['mean_net']):>9} {money(kept['mean_net']):>10} {data['winners_removed']:12} "
                      f"{data['losers_removed']:15} {data['unknown']:8}")
                print(f"    Same-known-sample net sum {ref['sum_net']:+.2f} -> {kept['sum_net']:+.2f}; "
                      f"sequential sample drawdown {ref['sample_drawdown']:.2f} -> {kept['sample_drawdown']:.2f}")
        print("  Drawdown is of hypothetical candidate outcomes in exit-time order, NOT portfolio drawdown.")
    print("\nExploratory evidence only. Samples overlap and share market days; do not infer significance from trade count alone.")
    print("Unknown inputs/outcomes are excluded, not counted as losses. Breadth is startup-only and is NOT tested as fresh entry breadth.")
    print("Keep parameters fixed during forward observation; validate promising filters on a later untouched period.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/shadow_validation.sqlite3")
    parser.add_argument("--date", help="one session (YYYY-MM-DD)")
    parser.add_argument("--since", help="first session (YYYY-MM-DD)")
    parser.add_argument("--until", help="last session; defaults to today")
    args = parser.parse_args()
    if args.date and (args.since or args.until):
        parser.error("use --date OR --since/--until")
    end = args.date or args.until or datetime.now(IST).date().isoformat()
    start = args.date or args.since or end
    try:
        datetime.fromisoformat(start)
        datetime.fromisoformat(end)
        if start > end:
            raise ValueError("start is after end")
        summarise(args.db, start, end)
    except (sqlite3.Error, ValueError) as exc:
        parser.exit(1, f"Shadow report unavailable: {exc}\n")


if __name__ == "__main__":
    main()
