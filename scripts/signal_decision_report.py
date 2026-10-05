"""Read-only gate coverage report; no claims about counterfactual executions."""
import argparse
from collections import defaultdict
from datetime import date
import json
from pathlib import Path
import sqlite3
from signal_diagnostics import summarise_decisions


def summarise(path, day):
    db = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='decisions'").fetchone():
            print('Decision evidence unavailable for this older database.'); return
        rows = [json.loads(r[0]) for r in db.execute('SELECT body FROM decisions WHERE day=? ORDER BY observed_at', (day,))]
    finally:
        db.close()
    print(f'\nSIGNAL DECISION COVERAGE — {day}')
    print('Sample: first evaluation per stock/minute; summaries use first scored stock/session observation, or first pre-score if none.')
    print('FAIL means a failed gate; UNKNOWN means missing evidence. Neither implies a missed profitable trade.')
    groups = defaultdict(list)
    for row in rows: groups[(row['strategy_id'],row['session'])].append(row)
    if not rows: print('No recorded decision observations.')
    for (strategy, session), records in sorted(groups.items()):
        result = summarise_decisions(records)
        print(f'{strategy} | {session}: {result["symbol_sessions"]} stock/session observations')
        print('  Policy:',json.dumps(records[0]['policy'],sort_keys=True))
        print('  Independent failures:',json.dumps(result['failures'],sort_keys=True))
        print('  Unknown evidence:',json.dumps(result['unknown'],sort_keys=True))
        print('  First terminal outcomes:',json.dumps(result['outcomes'],sort_keys=True))
    print('Sizing, hold continuity and portfolio competition not reached are NOT_EVALUATED, never assumed passed.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',default='data/shadow_validation.sqlite3')
    parser.add_argument('--date',required=True,type=date.fromisoformat)
    args=parser.parse_args()
    summarise(args.db,args.date.isoformat())
