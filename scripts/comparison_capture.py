"""Bounded full-arrival research tape. No orders, notifications or credential storage.

Records the inputs *before* each normal tick batch, including REST scan ticks.
This is the monitored universe, not the whole exchange order book. An incomplete
tape must never be presented as a complete-session comparison.
"""
import copy
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import logging
import math
from pathlib import Path
import queue
import sqlite3
import threading
import uuid
import zlib

from capture_segments import (SegmentWriter, CaptureError, LAYOUT, DEFAULT_DATA_BYTES,
                              DEFAULT_SEGMENT_BYTES, MAX_FRAME_BYTES, validated_records, CLOCK_SCHEMA, CaptureOrder)

LOG = logging.getLogger(__name__)
VERSION = 'execution-compare-v1'
SOURCE_FILES = ('intraday_signals.py', 'order_manager.py', 'signal_evidence.py',
                'signal_runtime.py', 'signal_recovery.py', 'signal_diagnostics.py',
                'execution_capital.py', 'trade_research.py', 'strategy_comparison.py',
                'comparison_capture.py', 'capture_segments.py', 'rvol_priority_audit.py', 'market_data_contract.py', 'kite_startup.py', 'universe_coverage.py')
CONFIG_NAMES = ('GAP_THRESHOLDS', 'SESSIONS', 'SECTOR_INDEX_MAP', 'MAX_RISK_PER_TRADE_INR',
                'MAX_CAPITAL_PER_TRADE', 'MAX_CONCURRENT_TRADES', 'MIN_PRIORITY_SCORE',
                'MAX_DAILY_LOSS_INR', 'MAX_POSITIONS', 'ATR_STOP_MULT', 'ATR_TARGET_MULT',
                'BROKERAGE_PER_TRADE', 'MIN_NET_PROFIT', 'MAX_DAILY_CAPITAL_INR',
                'MIN_TICKET_INR', 'DESK_CAPITAL_INR', 'GAP_HOLD_MINUTES')
GLOBAL_FIELDS = ('ctx', 'market_direction', 'vix_signal', 'sector_quotes', 'sentiment',
                 'breadth', 'market_context', 'signal_weights', 'fo_eligible_symbols')
SYMBOL_FIELDS = ('all_stocks', 'key_levels', 'rvol_baseline', 'rvol_provenance',
                 'prev_close', 'today_open', 'open_captured', 'vwap', 'cum_vol',
                 'cum_tp_vol', 'or_high', 'or_low', 'or_set', '_shadow_opening')


def encode(value):
    if isinstance(value, datetime): return {'@datetime': value.isoformat()}
    if isinstance(value, date): return {'@date': value.isoformat()}
    if isinstance(value, (set, frozenset)): return sorted(value)
    if isinstance(value, Decimal): return float(value)
    raise TypeError(type(value).__name__)


def decode(value):
    if set(value) == {'@datetime'}: return datetime.fromisoformat(value['@datetime'])
    if set(value) == {'@date'}: return date.fromisoformat(value['@date'])
    return value


def dumps(value):
    return json.dumps(value, default=encode, allow_nan=False, sort_keys=True, separators=(',', ':'))


def source_hashes():
    root = Path(__file__).parent
    return {p: hashlib.sha256((root / p).read_text(encoding='utf-8').encode('utf-8')).hexdigest() for p in SOURCE_FILES}


def capture_frame(engine, ticks, now):
    symbols = {engine.rev_tokens.get(t.get('instrument_token')) for t in ticks}
    symbols.discard(None)
    rows = {}
    for symbol in sorted(symbols):
        if symbol not in engine.all_stocks: continue
        values = {name: getattr(engine, name).get(symbol) for name in SYMBOL_FIELDS}
        # Unset opening-range infinity means unavailable, never a price.
        if isinstance(values['or_low'], float) and not math.isfinite(values['or_low']):
            values['or_low'] = None
        values.update(token=engine.token_map[symbol], long=symbol in engine.long_map,
                      short=symbol in engine.short_map)
        rows[symbol] = values
    return copy.deepcopy(dict(at=now, ticks=ticks, symbols=rows,
                              context={k: getattr(engine, k) for k in GLOBAL_FIELDS}))


class ComparisonTape:
    """Single writer; bounded queue and disk quota. Failures affect research only."""
    def __init__(self, path, metadata, baseline_loader, *, max_bytes=DEFAULT_DATA_BYTES,
                 segment_bytes=DEFAULT_SEGMENT_BYTES, queue_size=128, queue_bytes=64*1024*1024,
                 directory_bytes=8*1024*1024*1024):
        if any(not isinstance(v,int) or isinstance(v,bool) or v<=0 for v in
               (max_bytes,segment_bytes,queue_size,queue_bytes,directory_bytes)):
            raise ValueError('Capture limits must be positive integers')
        self.path = Path(path)
        self.metadata = copy.deepcopy(metadata)
        self.order = CaptureOrder() if metadata.get("clock_schema") == CLOCK_SCHEMA else None
        self.loader = baseline_loader
        self.max_bytes = max_bytes
        self.segment_bytes = segment_bytes
        self.directory_bytes = directory_bytes
        self.queue_bytes = queue_bytes
        self.pending_bytes = 0
        self.pending_lock = threading.RLock()
        self.loss_counts = {}
        self.first_lost_at = None
        self.failure_at = None
        self.queue = queue.Queue(maxsize=queue_size)
        self.stop = threading.Event()
        self.failed = None
        self.dropped = 0
        self.worker = threading.Thread(target=self._write, name='comparison-tape', daemon=True)
        self.worker.start()

    @classmethod
    def start(cls, engine, instruments):
        from order_manager import IST
        now = datetime.now(IST)
        ns = engine._evaluate_signal.__globals__
        metadata = dict(version=VERSION, clock_schema=CLOCK_SCHEMA, day=now.date().isoformat(), started_at=now,
            strategy_id=engine.strategy_id, source_hashes=source_hashes(),
            config={k: ns[k] for k in CONFIG_NAMES},
            limits=engine.execution.snapshot()['limits'],
            instruments=[{k: i[k] for k in ('tradingsymbol', 'exchange', 'segment', 'tick_size', 'instrument_token')
                          if k in i} for i in instruments if i.get('segment') == 'NSE'],
            initial_execution_flat=engine.execution.snapshot().get('flat', False),
            initial_execution_trades=len(engine.execution.snapshot().get('trades', {})))
        symbols = list(engine.rvol_baseline)
        startup = getattr(engine, 'startup_snapshot', None)
        metadata['startup_inputs'] = ({'snapshot_id': startup['snapshot_id'], 'manifest': startup['manifest']}
                                      if startup else {'version': 'overnight'})
        def loader():
            if startup is not None:
                return copy.deepcopy(startup['research'])
            import psycopg2
            from rvol_priority_audit import verify_neon
            # Connection settings are used only here, never put in tape metadata.
            con = psycopg2.connect(ns['NEON_URL'], connect_timeout=5)
            try:
                con.set_session(readonly=True)
                with con.cursor() as cur: cur.execute("SET statement_timeout = '15s'")
                return verify_neon(con, now.date(), symbols)
            finally:
                con.close()
        path = Path('data/comparison') / f'{now:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.sqlite3'
        return cls(path, metadata, loader)

    def _loss(self, reason, now):
        self.dropped += 1
        self.loss_counts[reason] = self.loss_counts.get(reason, 0)+1
        self.first_lost_at = self.first_lost_at or now

    def capture(self, engine, ticks, now, *, clock_sample=None):
        # Serialize close against an in-flight snapshot; the writer never holds
        # this lock during disk I/O. Lightweight offline fixtures omit the lock.
        with getattr(self,'pending_lock',threading.RLock()):
            return self._capture_locked(engine,ticks,now,clock_sample=clock_sample)

    def _capture_locked(self, engine, ticks, now, *, clock_sample=None):
        if self.failed or self.stop.is_set(): return
        try:
            # Fast rejection: never block the trading callback on a full queue.
            if self.queue.full(): raise queue.Full
            order = None
            if getattr(self, 'order', None) is not None:
                if not isinstance(clock_sample, tuple) or len(clock_sample) != 2:
                    raise CaptureError('missing_paired_clock_sample')
                next_order = copy.copy(self.order)
                order = next_order.stamp(now, *clock_sample)
            frame = capture_frame(engine, ticks, now)
            if order is not None:
                frame['capture_order'] = order
                if order['clock_discontinuity']:
                    LOG.warning('Comparison clock offset step at %s; raw observations retained, replay quarantined', now.isoformat())
                elif order['sampling_uncertain'] or order['raw_wall_regression']:
                    LOG.warning('Comparison clock evidence uncertain at %s; raw observations retained, replay quarantined', now.isoformat())
            raw = dumps(frame).encode('utf-8')
            if len(raw) > MAX_FRAME_BYTES:
                self._loss('frame_size_limit',now)
                return
            with self.pending_lock:
                if self.failed or self.stop.is_set(): return
                if self.pending_bytes+len(raw) > self.queue_bytes:
                    self._loss('queue_byte_limit',now)
                    return
                self.queue.put_nowait((raw,now,order))
                if order is not None:self.order = next_order
                self.pending_bytes += len(raw)
        except queue.Full:
            # Compatibility with lightweight __new__ fixtures as well as startup.
            if not hasattr(self,'loss_counts'): self.loss_counts = {}; self.first_lost_at = None
            self._loss('queue_full',now)
            if self.dropped == 1: LOG.warning('Comparison tape queue full; replay coverage incomplete')
        except Exception as exc:
            self.failed = type(exc).__name__
            self.failure_at = now
            LOG.warning('Comparison capture failed (%s); active strategy unchanged', self.failed)

    def _write(self):
        writer = None
        detail = None
        at = None
        try:
            writer = SegmentWriter(self.path,self.metadata,dumps,max_bytes=self.max_bytes,
                segment_bytes=self.segment_bytes,directory_bytes=self.directory_bytes)
            writer.checkpoint(baselines=self.loader())
            while not self.stop.is_set() or not self.queue.empty():
                try: item = self.queue.get(timeout=.1)
                except queue.Empty:
                    if self.failed: break
                    continue
                if self.failed: break
                order = None
                if isinstance(item,tuple):
                    raw,at = item[:2]
                    if len(item) == 3:order = item[2]
                    with self.pending_lock:self.pending_bytes -= len(raw)
                else:  # Offline fixtures may enqueue an already detached frame.
                    at = item['at']; raw = dumps(item).encode('utf-8')
                with self.pending_lock:
                    writer.meta.update(dropped=self.dropped,loss_counts=dict(self.loss_counts),
                                       first_lost_at=self.first_lost_at)
                writer.append(raw,at,capture_order=order)
        except Exception as exc:
            self.failed = type(exc).__name__
            detail = exc.code if isinstance(exc,CaptureError) else type(exc).__name__
            self.failure_at = at
            LOG.warning('Comparison tape stopped: %s; active strategy unchanged',detail)
        finally:
            if writer is not None:
                try:
                    with self.pending_lock:
                        final = dict(dropped=self.dropped,error=self.failed,error_detail=detail,
                            first_lost_at=self.first_lost_at,loss_counts=dict(self.loss_counts),
                            recording_stopped_at=self.failure_at,
                            queued_not_written=self.queue.qsize())
                    writer.finish(**final)
                except Exception as exc:
                    self.failed = self.failed or type(exc).__name__
                    LOG.warning('Comparison finalization failed (%s); manifest remains incomplete',type(exc).__name__)
                finally:writer.close()
            # Release abandoned queue memory; the manifest retains its count.
            with self.pending_lock:
                while not self.queue.empty():
                    try:self.queue.get_nowait()
                    except queue.Empty:break
                self.pending_bytes = 0

    def close(self):
        with self.pending_lock:self.stop.set()
        self.worker.join(timeout=10)
        LOG.info('Comparison tape %s: %s; dropped=%d. Run scripts/strategy_comparison.py --tape "%s" after shutdown.',
                 'incomplete' if self.failed or self.dropped or self.worker.is_alive() else 'saved',
                 self.path, self.dropped, self.path)


def read_metadata(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        return json.loads(db.execute('SELECT body FROM metadata WHERE id=1').fetchone()[0], object_hook=decode)
    finally:
        db.close()


def read_frames(path):
    meta = read_metadata(path)
    if meta.get('capture_layout') == LAYOUT:
        for raw in validated_records(path,meta):
            yield json.loads(raw,object_hook=decode)
        return
    if meta.get('capture_layout') is not None:
        raise ValueError('Unsupported capture layout')
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        for (body,) in db.execute('SELECT body FROM frames ORDER BY seq'):
            yield json.loads(zlib.decompress(body), object_hook=decode)
    finally:
        db.close()


def run_comparison_report(execution):
    """Run once after stopped, flat execution; bounded child process, local only."""
    import subprocess
    import sys
    tape = getattr(execution, 'comparison_tape', None)
    if tape is None: return 'skipped'
    if (not execution.snapshot().get('flat') or tape.worker.is_alive()
            or tape.failed or tape.dropped):
        LOG.warning('Comparison report skipped: capture incomplete or execution not flat')
        return 'skipped'
    try:
        subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).with_name('strategy_comparison.py')),
                        '--tape', str(tape.path.resolve())], check=True, timeout=600)
        return 'ok'
    except (OSError, subprocess.SubprocessError) as exc:
        LOG.warning('Comparison report incomplete (%s); tape retained at %s; retry offline',
                    type(exc).__name__, tape.path)
        return 'failed'

