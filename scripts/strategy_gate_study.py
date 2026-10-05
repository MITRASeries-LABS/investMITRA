"""Predefined gate ablations on frozen markouts; never an execution backtest."""
import argparse
import copy
from datetime import date
import json
from shadow_validation import VERSION, SPEC
from shadow_validation_report import read_report, metrics

VARIANTS = ('baseline', 'without_score_floor', 'without_rvol_floor',
            'without_priority_floor', 'without_rvol_and_priority', 'without_session_discount')
NAMES = ('gap', 'blended', 'rvol', 'priority', 'gap_type', 'sector', 'atr')


def selected(features, variant):
    if variant not in VARIANTS: raise ValueError('Unknown predefined variant')
    gates = copy.deepcopy(features.get('gate_diagnostics', {}))
    if any(gates.get(name, {}).get('status') not in ('PASS','FAIL') for name in NAMES):
        return None
    omit = {'without_score_floor': {'blended'}, 'without_rvol_floor': {'rvol'},
            'without_priority_floor': {'priority'}, 'without_rvol_and_priority': {'rvol','priority'}}.get(variant,set())
    if variant == 'without_session_discount':
        try:
            details = features['details']; policy = features['policy']
            score = .4*features['quality'] + .6*details['opportunity_before_session']
            priority = round(details['rvol']*abs(features['gap_pct'])*score/100,3)
            gates['blended']['status'] = 'PASS' if score >= policy['blended_min'] else 'FAIL'
            gates['priority']['status'] = 'PASS' if priority >= policy['priority_min'] else 'FAIL'
        except (KeyError,TypeError,ValueError):
            return None
    return all(gates[k]['status']=='PASS' for k in NAMES if k not in omit)


def summarise(path, start, end):
    groups, health, specs = read_report(path,start,end)
    print(f'\nGATE ABLATION STUDY {start} to {end}')
    print('Frozen first scored observations only; seven independent gates, NOT full-policy eligibility or realised P&L.')
    print('Hold continuity, sizing, exits, capital competition and portfolio drawdown are not simulated.')
    print('Use later untouched sessions for validation; these alternatives never change engine configuration.')
    if any(r['error'] or r['dropped_events'] for r in health): print('INCOMPLETE CAPTURE: observer errors/drops present.')
    for key, rows in sorted(groups.items()):
        print(' | '.join(key))
        if key[0] != VERSION or json.loads(specs.get(key[0], '{}')) != SPEC:
            print('Unsupported study specification; skipped.'); continue
        for row in rows: row['decoded'] = json.loads(row['features'])
        complete=[r for r in rows if r['status']=='COMPLETE']
        # Paired common-known cohort across every predefined variant.
        known=[r for r in complete if all(selected(r['decoded'],v) is not None for v in VARIANTS)]
        print(f'Common-known observations: {len(known)}; unknown: {len(complete)-len(known)}')
        for variant in VARIANTS:
            kept=[r for r in known if selected(r['decoded'],variant)]
            base,stress=metrics(kept),metrics(kept,'stress_net')
            print(f'  {variant}: kept={len(kept)} base_mean={base["mean_net"]} stress_mean={stress["mean_net"]}')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',default='data/shadow_validation.sqlite3')
    parser.add_argument('--start',required=True,type=date.fromisoformat)
    parser.add_argument('--end',required=True,type=date.fromisoformat)
    args=parser.parse_args()
    summarise(args.db,args.start.isoformat(),args.end.isoformat())
