"""Offline, predeclared portfolio comparison on forward-captured market inputs.

Uses IntradayEngine and AutoOrderManager, never KiteBroker, live journals or
network data. Observed-quote execution is hypothetical: it cannot reconstruct
unobserved prices, liquidity, queue position, or the broker's actual fills.
"""
import argparse
from collections import Counter
import copy
from datetime import datetime, timedelta
import json
import logging
import math
from pathlib import Path
import tempfile

from comparison_capture import (VERSION, SOURCE_FILES, dumps, read_frames,
                                read_metadata, source_hashes)
from order_manager import AutoOrderManager, Journal, PaperBroker, IST, TERMINAL
from signal_diagnostics import session_policy, reason_code

# Frozen hypotheses, not calibrated recommendations. Cross the two baseline
# choices with gate/rank policy to separate the causes. Only one lower-RVOL
# hypothesis, rather than searching a grid against the same outcomes.
VARIANTS = {
    'mean_gated': dict(baseline='mean', priority_gate=True, rvol=None),
    'median_gated': dict(baseline='median', priority_gate=True, rvol=None),
    'mean_rank': dict(baseline='mean', priority_gate=False, rvol=None),
    'median_rank': dict(baseline='median', priority_gate=False, rvol=None),
    'median_rank_rvol2': dict(baseline='median', priority_gate=False,
                             rvol={'momentum': 2., 'choppy': 3., 'afternoon': 2.}),
}
EXECUTION_CADENCE_SECONDS = 2
COST_CASES = {'base': (80., 5.), 'stress': (160., 10.)}


def variant_policy(variant, session, gap, priority):
    policy = session_policy(session, gap, priority)
    if not variant['priority_gate']: policy['priority_min'] = 0.
    if variant['rvol'] is not None: policy['rvol_min'] = variant['rvol'][session]
    return policy


def baseline_available(row, captured, day):
    """Same input universe for all variants; no silent median->mean fallback."""
    try:
        if row['status'] != 'recomputed_daily_baseline' or row['sessions'] < 10:
            return False
        if not row['first_date'] <= row['last_date'] < day: return False
        if row['excluded_conflicting_days']: return False
        return (math.isfinite(float(row['median_volume'])) and row['median_volume'] > 0
                and math.isclose(float(captured), row['mean_volume'], rel_tol=1e-8, abs_tol=.01))
    except (KeyError, TypeError, ValueError):
        return False


class ReplayClock:
    def __init__(self, at): self.at = at
    def __call__(self): return self.at


class QuoteTape:
    def __init__(self): self.values = {}
    def quote(self, instruments):
        return {s: copy.deepcopy(self.values[s]) for s in instruments if s in self.values}
    def update(self, frame):
        tokens = {v['token']: s for s, v in frame['symbols'].items()}
        for tick in frame['ticks']:
            symbol = tokens.get(tick.get('instrument_token'))
            if not symbol: continue
            q = copy.deepcopy(tick)
            # Retain the *observed exchange timestamp*, never substitute receipt
            # time to make an old price fresh. REST includes timestamp directly.
            q['timestamp'] = tick.get('timestamp') or tick.get('exchange_timestamp') or tick.get('last_trade_time')
            previous = self.values.get('NSE:' + symbol, {})
            if 'upper_circuit_limit' not in q and 'upper_circuit_limit' in previous:
                q['upper_circuit_limit'] = previous['upper_circuit_limit']
            self.values['NSE:' + symbol] = q


class ReplayBroker(PaperBroker):
    """Same paper order book, with adverse fills and strict quote freshness."""
    def __init__(self, quotes, journal, clock, slippage_bps):
        super().__init__(quotes, journal)
        self.clock, self.slippage_bps = clock, slippage_bps
        self.missing_price_attempts = 0

    def _match(self, order):
        if order['status'] in TERMINAL: return
        quote = self.last.get('NSE:' + order['tradingsymbol'], {})
        at = quote.get('timestamp')
        if isinstance(at, str):
            try: at = datetime.fromisoformat(at)
            except ValueError: at = None
        if isinstance(at, datetime) and at.tzinfo is None: at = at.replace(tzinfo=IST)
        px = float(quote.get('last_price') or 0)
        if (not isinstance(at, datetime) or not -2 <= (self.clock()-at).total_seconds() <= 10
                or not math.isfinite(px) or px <= 0):
            self.missing_price_attempts += 1
            return
        buy = order['transaction_type'] == 'BUY'
        fill = px * (1 + (1 if buy else -1)*self.slippage_bps/10000)
        typ = order['order_type']
        cross = typ == 'MARKET'
        if typ == 'LIMIT': cross = fill <= order['price'] if buy else fill >= order['price']
        if typ == 'SL-M': cross = px >= order['trigger_price'] if buy else px <= order['trigger_price']
        if cross:
            order.update(status='COMPLETE', filled_quantity=order['quantity'], average_price=fill)
        elif order.get('validity') == 'IOC': order['status'] = 'CANCELLED'


def make_engine(module, frame, meta, variant, manager, clock):
    context = frame['context']
    engine = module.IntradayEngine([], [], {}, {}, context['market_direction'],
                                  context['ctx'], {}, {}, {}, {})
    engine.execution = manager
    engine._entry_blocked = False
    engine.strategy_id = meta['strategy_id'] + '/' + VERSION
    engine._now = clock
    engine._admission_policy = lambda session, gap: variant_policy(
        variant, session, gap, module.MIN_PRIORITY_SCORE)
    return engine


def apply_inputs(engine, frame, meta, variant, exclusions):
    for name, value in frame['context'].items(): setattr(engine, name, copy.deepcopy(value))
    for symbol, values in frame['symbols'].items():
        engine.token_map[symbol] = values['token']
        engine.rev_tokens[values['token']] = symbol
        for name, value in values.items():
            if name in {'token', 'long', 'short'}: continue
            target = getattr(engine, name)
            if value is None: target.pop(symbol, None)
            else: target[symbol] = copy.deepcopy(value)
        stock = engine.all_stocks.get(symbol, {})
        for name in ('long', 'short'):
            mapping = getattr(engine, name + '_map')
            if values[name]: mapping[symbol] = stock
            else: mapping.pop(symbol, None)
        history = meta['baselines'].get(symbol, {})
        if not baseline_available(history, values.get('rvol_baseline'), meta['day']):
            exclusions.add(symbol)
            engine.rvol_baseline.pop(symbol, None)
        elif variant['baseline'] == 'median':
            engine.rvol_baseline[symbol] = history['median_volume']
            engine.rvol_provenance.setdefault(symbol, {}).update(method='median_positive_daily_volume')


class PortfolioRun:
    def __init__(self, module, meta, frame, variant, name, path, cost_case):
        self.clock = ReplayClock(frame['at'])
        self.quotes = QuoteTape()
        cost, slip = COST_CASES[cost_case]
        self.journal = Journal(path, 'research-' + name, 'auto_paper')
        # Disposable offline journals: production journals retain synchronous FULL.
        self.journal.db.execute('PRAGMA synchronous=OFF')
        self.broker = ReplayBroker(self.quotes, self.journal, self.clock, slip)
        limits = {k: meta['limits'][k] for k in ('daily_cap', 'min_ticket', 'max_ticket',
                  'max_risk', 'max_daily_loss', 'max_positions')}
        self.manager = AutoOrderManager(self.broker, self.journal, meta['instruments'],
            clock=self.clock, alerts=lambda message: None, cost_reserve=cost, **limits)
        self.manager.step()
        self.engine = make_engine(module, frame, meta, variant, self.manager, self.clock)
        self.variant, self.meta = variant, meta
        self.exclusions = set()
        self.reason_counts = Counter()
        self.next_cycle = frame['at']
        self.peak = self.drawdown = 0.
        self.unknown_equity_cycles = 0
        self.max_exposure = self.max_active = 0
        self.samples = 0

    def advance(self, until):
        while self.next_cycle < until:
            self.clock.at = self.next_cycle
            self.manager.step()
            view = self.manager.snapshot()
            equity = view['combined_net']
            if equity is None: self.unknown_equity_cycles += 1
            else:
                self.peak = max(self.peak, equity)
                self.drawdown = max(self.drawdown, self.peak-equity)
            self.max_exposure = max(self.max_exposure, view['exposure'])
            self.max_active = max(self.max_active, view['active_count'])
            self.next_cycle += timedelta(seconds=EXECUTION_CADENCE_SECONDS)

    def frame(self, frame):
        self.advance(frame['at'])
        self.clock.at = frame['at']
        apply_inputs(self.engine, frame, self.meta, self.variant, self.exclusions)
        self.quotes.update(frame)
        self.engine._on_tick_locked(None, frame['ticks'])
        self.samples += 1
        reason = getattr(self.engine, '_decision_reason', None)
        if reason: self.reason_counts[reason_code(reason)] += 1  # last evaluation per captured batch, not trade counts

    def result(self):
        view = self.manager.snapshot()
        trades = list(view['trades'].values())
        filled = [t for t in trades if self.manager._filled(t)]
        closed = [t for t in filled if t.get('closed_at') and not self.manager._remaining(t)]
        nets = [self.manager._gross(t)-self.manager._cost(t) for t in closed]
        return dict(filled=len(filled), closed=len(closed), flat=view['flat'], gross=view['gross'],
            provisional_net=view['net'], mean_closed_net=sum(nets)/len(nets) if nets else None,
            closed_wins=sum(n > 0 for n in nets),
            win_rate=sum(n > 0 for n in nets)/len(nets) if nets else None,
            turnover=view['tickets'], cost_allowances=view['costs'], observed_equity_drawdown=self.drawdown,
            unknown_equity_cycles=self.unknown_equity_cycles, max_exposure=self.max_exposure,
            max_active=self.max_active, halt=view['reason'], input_excluded_symbols=sorted(self.exclusions),
            missing_price_attempts=self.broker.missing_price_attempts,
            last_batch_reasons=dict(self.reason_counts), snapshot=view)


def validate_tape(meta):
    if meta.get('version') != VERSION: raise ValueError('Unsupported comparison tape version')
    if not meta.get('closed') or meta.get('dropped') or meta.get('error'):
        raise ValueError('Incomplete capture: open writer, dropped events or capture error')
    if meta.get('source_hashes') != source_hashes():
        raise ValueError('Replay source differs from capture; use the exact captured code revision')
    if not meta.get('initial_execution_flat') or meta.get('initial_execution_trades'):
        raise ValueError('Capture started with existing execution state; a new full session is required')
    if not meta.get('frames'): raise ValueError('No market frames recorded')


def run(tape, module=None):
    meta = read_metadata(tape)
    validate_tape(meta)
    if module is None:
        import intraday_signals as module
    old = {k: getattr(module, k) for k in meta['config']}
    for k, value in meta['config'].items(): setattr(module, k, copy.deepcopy(value))
    portfolios = {}
    first = last = None
    count = 0
    max_gap = 0.
    with tempfile.TemporaryDirectory(prefix='investmitra-comparison-') as directory:
        try:
            for frame in read_frames(tape):
                count += 1
                at = frame['at']
                if at.tzinfo is None or at.astimezone(IST).date().isoformat() != meta['day']:
                    raise ValueError('Frame date/timezone mismatch')
                if last is not None and at < last: raise ValueError('Capture clock moved backwards')
                if last is not None:
                    start = at.replace(hour=9, minute=15, second=0, microsecond=0)
                    end = at.replace(hour=15, minute=5, second=0, microsecond=0)
                    max_gap = max(max_gap, max(0., (min(at,end)-max(last,start)).total_seconds()))
                if first is None:
                    first = at
                    for name, variant in VARIANTS.items():
                        for case in COST_CASES:
                            key = name + '/' + case
                            portfolios[key] = PortfolioRun(module, meta, frame, variant, key,
                                Path(directory) / (name + '_' + case + '.sqlite3'), case)
                last = at
                for portfolio in portfolios.values(): portfolio.frame(frame)
            if last is None: raise ValueError('Tape frame count does not match contents')
            if count != meta['frames']: raise ValueError('Tape frame count does not match contents')
            for portfolio in portfolios.values():
                # Reconcile only up to the final observed instant. Never extend a
                # stopped tape to 3PM, fill at an old quote, or invent an exit.
                portfolio.advance(last + timedelta(microseconds=1))
            start_minute = first.hour*60+first.minute
            end_minute = last.hour*60+last.minute
            results = {k: p.result() for k, p in portfolios.items()}
            complete_span = start_minute <= 555 and end_minute >= 904
            comparison_complete = (complete_span and max_gap <= 60 and
                all(r['flat'] and r['unknown_equity_cycles']==0 for r in results.values()))
            return dict(version=VERSION, day=meta['day'], strategy_id=meta['strategy_id'],
                source_hashes=meta['source_hashes'], variants=VARIANTS, cost_cases=COST_CASES,
                first_at=first, last_at=last,
                max_global_arrival_gap_seconds=max_gap,
                full_session_span=complete_span, comparison_complete=comparison_complete,
                caveats=['Observed universe and captured arrival order only; no counterfactual universe discovery.',
                         'Full-size LTP fills with adverse slippage, not a liquidity/market impact model.',
                         'Uniform elapsed-session RVOL, not historical same-time volume seasonality.',
                         'Mean and median use unadjusted prior NSE volumes; corporate-action integrity not proved.',
                         'The control is also a simulation; replay cadence and fills can differ from the actual paper session.',
                         'No fabricated fills for missing/stale quotes; unresolved positions invalidate total-result comparison.',
                         'Do not infer statistical significance from correlated trades or tune on this session.'],
                results=results)
        finally:
            for portfolio in portfolios.values(): portfolio.journal.close()
            for k, value in old.items(): setattr(module, k, value)


def print_report(result):
    print('\nCONTROLLED STRATEGY COMPARISON', result['day'], result['version'])
    print('Hypothetical portfolios using the actual signal/execution rules. Active strategy unchanged.')
    print('Session span complete:', result['full_session_span'])
    print('Complete portfolio comparison:', result['comparison_complete'])
    if not result['comparison_complete']:
        print('INCOMPLETE: partial/gapped session or unresolved/missing position marks; exclude from full-session validation.')
    print('Largest gap between received batches (seconds):', result['max_global_arrival_gap_seconds'])
    print(f"{'Variant / cost':<32} {'Filled':>6} {'Closed':>6} {'Net*':>10} {'Mean closed':>12} {'DD*':>10} {'Flat':>5}")
    for name, r in result['results'].items():
        mean = '--' if r['mean_closed_net'] is None else f"{r['mean_closed_net']:.2f}"
        print(f"{name:<32} {r['filled']:6} {r['closed']:6} {r['provisional_net']:10.2f} {mean:>12} {r['observed_equity_drawdown']:10.2f} {str(r['flat']):>5}")
        if r['unknown_equity_cycles'] or r['input_excluded_symbols'] or not r['flat']:
            print('  Coverage:', {k: r[k] for k in ('unknown_equity_cycles','input_excluded_symbols','flat')})
    print('* Net uses the frozen per-filled-trade allowance; DD uses observed marked equity and excludes unknown marks.')
    for warning in result['caveats']: print(warning)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tape', required=True)
    args = parser.parse_args()
    # Quiet simulation messages: never send Telegram, Neon or broker API calls.
    logging.getLogger('order_manager').setLevel(logging.ERROR)
    logging.getLogger('intraday_signals').setLevel(logging.ERROR)
    result = run(args.tape)
    output = Path(args.tape).with_suffix('.comparison.json')
    output.write_text(dumps(result), encoding='utf-8')
    print_report(result)
    print('Detailed hypothetical fills and assumptions:', output)


if __name__ == '__main__': main()
