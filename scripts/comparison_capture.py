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

LOG = logging.getLogger(__name__)
VERSION = 'execution-compare-v1'
SOURCE_FILES = ('intraday_signals.py', 'order_manager.py', 'signal_evidence.py',
                'signal_runtime.py', 'signal_recovery.py', 'signal_diagnostics.py',
                'execution_capital.py', 'trade_research.py', 'strategy_comparison.py',
                'comparison_capture.py', 'rvol_priority_audit.py', 'market_data_contract.py')
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
    def __init__(self, path, metadata, baseline_loader, *, max_bytes=512*1024*1024, queue_size=2048):
        self.path = Path(path)
        self.metadata = copy.deepcopy(metadata)
        self.loader = baseline_loader
        self.max_bytes = max_bytes
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
        metadata = dict(version=VERSION, day=now.date().isoformat(), started_at=now,
            strategy_id=engine.strategy_id, source_hashes=source_hashes(),
            config={k: ns[k] for k in CONFIG_NAMES},
            limits=engine.execution.snapshot()['limits'],
            instruments=[{k: i[k] for k in ('tradingsymbol', 'exchange', 'segment', 'tick_size', 'instrument_token')
                          if k in i} for i in instruments if i.get('segment') == 'NSE'],
            initial_execution_flat=engine.execution.snapshot().get('flat', False),
            initial_execution_trades=len(engine.execution.snapshot().get('trades', {})))
        symbols = list(engine.rvol_baseline)
        def loader():
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

    def capture(self, engine, ticks, now):
        if self.failed or self.stop.is_set(): return
        try:
            self.queue.put_nowait(capture_frame(engine, ticks, now))
        except queue.Full:
            self.dropped += 1
            if self.dropped == 1: LOG.warning('Comparison tape queue full; replay coverage incomplete')
        except Exception as exc:
            self.failed = type(exc).__name__
            LOG.warning('Comparison capture failed (%s); active strategy unchanged', self.failed)

    def _write(self):
        db = None
        frames = size = 0
        first = last = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path)
            db.execute('CREATE TABLE metadata (id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('CREATE TABLE frames (seq INTEGER PRIMARY KEY, body BLOB NOT NULL)')
            meta = dict(self.metadata, closed=False)
            db.execute('INSERT INTO metadata VALUES (1,?)', (dumps(meta),)); db.commit()
            meta['baselines'] = self.loader()
            db.execute('UPDATE metadata SET body=? WHERE id=1', (dumps(meta),)); db.commit()
            while not self.stop.is_set() or not self.queue.empty():
                try: frame = self.queue.get(timeout=.1)
                except queue.Empty: continue
                if self.failed: break
                body = zlib.compress(dumps(frame).encode('utf-8'), level=1)
                size += len(body)
                if size > self.max_bytes:
                    raise ValueError('tape_size_limit')
                frames += 1
                first = first or frame['at']
                last = frame['at']
                db.execute('INSERT INTO frames VALUES (?,?)', (frames, body))
                if frames % 100 == 0:
                    db.commit()
                    pages = db.execute('PRAGMA page_count').fetchone()[0]
                    page_size = db.execute('PRAGMA page_size').fetchone()[0]
                    if pages * page_size > self.max_bytes:
                        raise ValueError('tape_database_size_limit')
            meta.update(closed=True, dropped=self.dropped, error=self.failed,
                        frames=frames, compressed_bytes=size, first_at=first, last_at=last)
            db.execute('UPDATE metadata SET body=? WHERE id=1', (dumps(meta),)); db.commit()
        except Exception as exc:
            self.failed = type(exc).__name__
            LOG.warning('Comparison tape unavailable (%s); active strategy unchanged', self.failed)
            if db is not None:
                try:
                    meta.update(closed=True, dropped=self.dropped, error=self.failed, frames=frames)
                    db.execute('UPDATE metadata SET body=? WHERE id=1', (dumps(meta),)); db.commit()
                except Exception: pass
        finally:
            if db is not None: db.close()

    def close(self):
        self.stop.set()
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
