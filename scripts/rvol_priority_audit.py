"""Read-only sampled admission audit. No broker calls, parameter changes or P&L replay."""
import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3

IST = timezone(timedelta(hours=5, minutes=30))
GATES = ('gap', 'blended', 'rvol', 'priority', 'gap_type', 'sector', 'atr', 'direction_vwap')
VARIANTS = {
    'baseline': set(),
    'without_rvol_floor': {'rvol'},
    'without_priority_floor': {'priority'},
    'without_rvol_and_priority': {'rvol', 'priority'},
}


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def audit_volume(row):
    """Reconcile captured operands, not independent verification of exchange truth."""
    d = row.get('details', {})
    p = d.get('rvol_baseline', {})
    issues = []
    volume, avg, fraction, expected, raw = [number(d.get(k)) for k in
        ('rvol_live_volume','rvol_avg_daily_volume','rvol_elapsed_fraction',
         'rvol_expected_volume','rvol_uncapped')]
    if any(v is None for v in (volume, avg, fraction, expected, raw)):
        return ['missing_or_nonfinite_volume_operands']
    if volume < 0 or avg <= 0 or fraction <= 0 or expected <= 0 or raw < 0:
        return ['invalid_volume_operands']
    if not math.isclose(expected, avg*fraction, rel_tol=1e-8, abs_tol=.01):
        issues.append('expected_volume_arithmetic_mismatch')
    if not math.isclose(raw, volume/expected, rel_tol=1e-8, abs_tol=1e-8):
        issues.append('rvol_arithmetic_mismatch')
    observed_volume = number(row.get('volume'))
    if observed_volume is None or not math.isclose(volume, observed_volume, abs_tol=.01):
        issues.append('decision_and_scoring_volume_disagree')
    if d.get('rvol_method') != 'linear_elapsed_session_fraction':
        issues.append('unsupported_rvol_method')
    else:
        at = datetime.fromtimestamp(row['observed_at'], IST)
        clock_fraction = max((at.hour*60+at.minute-555)/375, .05)
        # Evaluation and score timestamps can straddle a minute boundary.
        if abs(fraction-clock_fraction) > 1/375 + 1e-8:
            issues.append('elapsed_fraction_clock_mismatch')
    if p.get('source') != 'NSE':
        issues.append('unverified_baseline_venue')
    try:
        first, last, as_of = [date.fromisoformat(p[k]) for k in ('first_date','last_date','as_of')]
        if not first <= last < as_of or as_of != date.fromisoformat(row['day']):
            issues.append('baseline_date_mismatch_or_lookahead')
    except (KeyError, TypeError, ValueError):
        issues.append('missing_baseline_dates')
    count = number(p.get('sample_days'))
    if count is None or count <= 0 or count != int(count):
        issues.append('invalid_baseline_sample_count')
    return issues


def policy_evidence(row):
    """Expose compounded RVOL demand; exact admission still uses saved gate results."""
    gates = row.get('gates', {})
    gap = number(gates.get('gap', {}).get('actual'))
    score = number(gates.get('blended', {}).get('actual'))
    rvol = number(gates.get('rvol', {}).get('actual'))
    p = row.get('policy', {})
    priority_min, rvol_min = number(p.get('priority_min')), number(p.get('rvol_min'))
    implied = None
    if gap is not None and score is not None and abs(gap)*score > 0 and priority_min is not None:
        implied = priority_min/(abs(gap)*score/100)
    return dict(rvol=rvol, rvol_floor=rvol_min, gap=gap, blended=score,
                priority=gates.get('priority', {}).get('actual'), priority_floor=priority_min,
                approximate_joint_rvol_floor=max(rvol_min,implied)
                if rvol_min is not None and implied is not None else None)


def analyse(records):
    """All saved minute samples; first crossing per symbol, never best outcome."""
    rows = sorted(records, key=lambda r: (r['observed_at'],r['symbol']))
    scored = [r for r in rows if r.get('gates')]
    known = [r for r in scored if all(r['gates'].get(k,{}).get('status') in ('PASS','FAIL') for k in GATES)]
    variants = {}
    for variant, omitted in VARIANTS.items():
        passing = [r for r in known if all(r['gates'][k]['status']=='PASS' for k in GATES if k not in omitted)]
        first = {}
        for row in passing:
            first.setdefault(row['symbol'],row)
        variants[variant] = dict(samples=len(passing), symbols=len(first), first=list(first.values()))
    issues, sector, resets, prior_volume = Counter(), {}, Counter(), {}
    for row in scored:
        issues.update(audit_volume(row))
        d = row.get('details', {})
        volume = number(d.get('rvol_live_volume'))
        sym = row['symbol']
        if volume is not None:
            if sym in prior_volume and volume < prior_volume[sym]:
                resets[sym] += 1
            prior_volume[sym] = volume
        if row['gates'].get('sector',{}).get('status') == 'UNKNOWN':
            sector[sym] = dict(sector=d.get('stock_sector'),status=d.get('sector_status'),
                              index=d.get('sector_index'),quote_at=d.get('sector_quote_at'))
    return dict(samples=len(rows), scored=len(scored), known=len(known),
                unknown=len(scored)-len(known), variants=variants,
                volume_issues=dict(issues), volume_decreases=dict(resets), sector_unknowns=sector)


def read_records(path, day):
    db = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='decisions'").fetchone():
            return []
        return [json.loads(r[0]) for r in db.execute('SELECT body FROM decisions WHERE day=? ORDER BY observed_at',(day,))]
    finally:
        db.close()


def read_health(path, day):
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='observer_health'").fetchone():
            return None
        start=datetime.fromisoformat(day).replace(tzinfo=IST).timestamp()
        rows=db.execute('SELECT dropped_events,error FROM observer_health WHERE updated_at>=? AND updated_at<?',
                        (start,start+86400)).fetchall()
        return dict(runs=len(rows),dropped_events=sum(r[0] for r in rows),errors=sum(bool(r[1]) for r in rows))
    finally:
        db.close()


def verify_neon(conn, day, symbols):
    """Recompute the existing prior-session daily baseline without changing it."""
    with conn.cursor() as cur:
        cur.execute('''WITH daily AS (
            SELECT cm.nse_symbol AS symbol,ep.trade_date,MAX(ep.volume) AS volume,
                   COUNT(DISTINCT ep.volume) AS volume_versions,COUNT(DISTINCT ep.isin) AS identities
            FROM investmitra.equity_prices ep JOIN investmitra.company_master cm ON cm.isin=ep.isin
            WHERE ep.source='NSE' AND ep.trade_date >= %s AND ep.trade_date < %s
              AND ep.volume>0 AND cm.nse_symbol=ANY(%s)
            GROUP BY cm.nse_symbol,ep.trade_date
        ) SELECT symbol,trade_date,volume,volume_versions,identities FROM daily ORDER BY symbol,trade_date''',
                    (day-timedelta(days=30),day,symbols))
        rows=cur.fetchall()
    groups=defaultdict(list)
    for symbol,session,volume,versions,identities in rows:
        groups[symbol].append((session,float(volume),versions==1 and identities==1))
    result={}
    for symbol in symbols:
        source=groups[symbol]
        valid=[r for r in source if r[2]]
        volumes=sorted(r[1] for r in valid)
        if not volumes:
            result[symbol]=dict(status='no_valid_prior_NSE_volume',excluded_conflicting_days=len(source));continue
        n=len(volumes); total=sum(volumes)
        result[symbol]=dict(status='recomputed_daily_baseline',sessions=n,
            first_date=min(r[0] for r in valid).isoformat(),last_date=max(r[0] for r in valid).isoformat(),
            mean_volume=total/n,median_volume=(volumes[(n-1)//2]+volumes[n//2])/2,
            min_volume=volumes[0],max_volume=volumes[-1],largest_day_share=volumes[-1]/total,
            excluded_conflicting_days=len(source)-n,corporate_action_adjusted=False)
    return result


def compare_baselines(records, recomputed):
    results={}
    for row in records:
        if not row.get('gates'): continue
        symbol=row['symbol']; d=row.get('details',{}); actual=number(d.get('rvol_avg_daily_volume'))
        historical=recomputed.get(symbol,{})
        current=number(historical.get('mean_volume'))
        key=(symbol,actual)
        results[key]=dict(symbol=symbol,recorded_mean=actual,recomputed_mean=current,
            status='unavailable' if actual is None or current is None else
            'match' if math.isclose(actual,current,rel_tol=1e-8,abs_tol=.01) else 'mismatch_or_source_revision')
    return list(results.values())


def summarise(path, day):
    rows = read_records(path,day)
    print(f'\nRVOL / PRIORITY AUDIT — {day}')
    print('ALL SAVED MINUTE SAMPLES, not every tick. Counts are correlated observations, not independent trades.')
    print('Eight diagnostic gates only; hold, sizing, execution and capital are NOT simulated. No hypothetical P&L assigned to new times.')
    print('Removing a gate retains recorded scores and every other gate; it does not rescore the stock.')
    print('Arithmetic consistency does not prove exchange data accuracy or same-time-of-day volume calibration.')
    health=read_health(path,day)
    print('Observer capture health:',json.dumps(health) if health else 'unavailable')
    if health and (health['dropped_events'] or health['errors']):
        print('INCOMPLETE CAPTURE: missing samples can hide gate crossings.')
    if not rows:
        print('No decision records available; no eligibility conclusion.'); return
    groups = defaultdict(list)
    for row in rows:
        groups[(row['strategy_id'],row['session'])].append(row)
    for (strategy,session), group in sorted(groups.items()):
        a = analyse(group)
        print(f'\n{strategy} | {session}: samples={a["samples"]}, scored={a["scored"]}, common-known={a["known"]}, unknown={a["unknown"]}')
        print('  Volume operand issues (sample counts):',json.dumps(a['volume_issues'],sort_keys=True))
        print('  Cumulative volume decreases (inspect corrections/resets):',json.dumps(a['volume_decreases'],sort_keys=True))
        print('  Latest missing sector evidence by symbol:',json.dumps(a['sector_unknowns'],sort_keys=True))
        for name,v in a['variants'].items():
            print(f'  {name}: gate-passing samples={v["samples"]}, unique symbols={v["symbols"]}')
            for row in v['first'][:10]:
                at = datetime.fromtimestamp(row['observed_at'],IST).strftime('%H:%M:%S')
                print(f'    FIRST {row["symbol"]}@{at} actual_outcome={row["outcome"]}: '+json.dumps(policy_evidence(row),sort_keys=True))
        print('  First crossings shown: up to 10 per variant. Joint RVOL floor is approximate because admission rounds RVOL/priority.')
    print('No threshold changes made. Missing samples and unscored periods cannot be assumed eligible or ineligible.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date',required=True,type=date.fromisoformat)
    parser.add_argument('--db',default='data/shadow_validation.sqlite3')
    parser.add_argument('--verify-neon',action='store_true',help='Optional read-only comparison with current stored prior-session NSE history')
    args=parser.parse_args()
    summarise(args.db,args.date.isoformat())
    if args.verify_neon:
        import os
        import psycopg2
        from dotenv import load_dotenv
        load_dotenv('.env.prod')
        records=read_records(args.db,args.date.isoformat())
        symbols=sorted({r['symbol'] for r in records if r.get('gates')})
        conn=psycopg2.connect(os.environ['CC_POSTGRES_URL'],connect_timeout=10,options='-c statement_timeout=15000')
        try:
            conn.set_session(readonly=True)
            baselines=verify_neon(conn,args.date,symbols)
        finally:
            conn.close()
        print('\nCURRENT STORED NSE HISTORY (may include revisions since the trading session):')
        concentrated=sorted(baselines.items(),key=lambda item:(-item[1].get('largest_day_share',0),item[0]))[:10]
        print('Highest single-day volume concentration (up to 10; diagnostic, not an exclusion rule):')
        print(json.dumps(dict(concentrated),sort_keys=True,indent=2))
        print('Missing/conflicting baseline histories:',json.dumps({s:r for s,r in baselines.items()
            if r.get('excluded_conflicting_days') or r['status']=='no_valid_prior_NSE_volume'},sort_keys=True))
        comparisons=compare_baselines(records,baselines)
        print('Captured baseline comparison counts:',dict(Counter(r['status'] for r in comparisons)))
        print('Non-matching captured baselines:',json.dumps([r for r in comparisons if r['status']!='match'],sort_keys=True))
        print('No automatic adjustment for outliers or corporate actions; no same-time intraday baseline inferred from daily bars.')
