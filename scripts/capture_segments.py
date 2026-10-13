"""Append-only bounded research segments with a SQLite manifest. No trading I/O.
The manifest stays compatible with comparison_capture metadata; frame readers
validate sealed files, byte lengths, SHA256 and contiguous sequence numbers.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import shutil
import struct
import zlib

LAYOUT = 'segments-v1'
HEADER = struct.Struct('!QI')  # global sequence, compressed payload length
DEFAULT_SEGMENT_BYTES = 64 * 1024 * 1024
DEFAULT_DATA_BYTES = 2 * 1024 * 1024 * 1024
MAX_FRAME_BYTES = 4 * 1024 * 1024
MANIFEST_BYTES = 4 * 1024 * 1024
MAX_SEGMENTS = 4096


class CaptureError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


CLOCK_SCHEMA = 'paired-arrival-v2'
CLOCK_STEP_TOLERANCE_NS = 250_000_000
WALL_QUANTUM_NS = 1_000  # datetime exposes microseconds; widen both bounds.
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def datetime_ns(at):
    if not isinstance(at, datetime) or at.tzinfo is None:
        raise CaptureError('invalid_capture_wall_time')
    delta = at - EPOCH
    return ((delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds) * 1000


class CaptureOrder:
    """Bound the wall/monotonic offset using samples around the SAME wall read.

    Scheduling stalls widen evidence intervals rather than inventing clock steps.
    Strategy time is never replaced. Wide brackets and raw wall regressions
    conservatively quarantine replay, separately from evidenced clock steps.
    """
    def __init__(self):
        self.seq = 0
        self.wall = self.before = self.ns = self.lo = self.hi = None
        self.discontinuities = self.uncertain_samples = self.wall_regressions = 0
        self.maximum_gap_seconds = 0.

    def stamp(self, at, before, after):
        if (type(before) is not int or type(after) is not int or before < 0
                or after < before or (self.ns is not None and
                (before < self.ns or after <= self.ns))):
            raise CaptureError('invalid_monotonic_capture_order')
        wall = datetime_ns(at)
        lo, hi = wall - after - WALL_QUANTUM_NS, wall - before + WALL_QUANTUM_NS
        delta_lo = None if self.lo is None else lo - self.hi
        delta_hi = None if self.hi is None else hi - self.lo
        step = (delta_lo is not None and
                (delta_lo > CLOCK_STEP_TOLERANCE_NS or delta_hi < -CLOCK_STEP_TOLERANCE_NS))
        uncertain = after - before > CLOCK_STEP_TOLERANCE_NS
        wall_delta = None if self.wall is None else wall - self.wall
        regression = wall_delta is not None and wall_delta < 0
        elapsed = None if self.ns is None else after - self.ns
        value = dict(seq=self.seq + 1, monotonic_before_ns=before, monotonic_ns=after,
                     wall_delta_ns=wall_delta, elapsed_ns=elapsed,
                     offset_change_min_ns=delta_lo, offset_change_max_ns=delta_hi,
                     clock_discontinuity=step, sampling_uncertain=uncertain,
                     raw_wall_regression=regression)
        self.seq += 1
        self.wall, self.before, self.ns, self.lo, self.hi = wall, before, after, lo, hi
        self.discontinuities += int(step)
        self.uncertain_samples += int(uncertain)
        self.wall_regressions += int(regression)
        if elapsed is not None:
            self.maximum_gap_seconds = max(self.maximum_gap_seconds, elapsed / 1e9)
        return value

    @property
    def replay_eligible(self):
        return not (self.discontinuities or self.uncertain_samples or self.wall_regressions)

    def verify_order(self, at, order):
        if not isinstance(order, dict):raise CaptureError('missing_capture_order')
        for key in ('seq', 'monotonic_before_ns', 'monotonic_ns'):
            if type(order.get(key)) is not int:raise CaptureError('invalid_capture_order')
        for key in ('clock_discontinuity', 'sampling_uncertain', 'raw_wall_regression'):
            if type(order.get(key)) is not bool:raise CaptureError('invalid_capture_order')
        for key in ('wall_delta_ns', 'elapsed_ns', 'offset_change_min_ns', 'offset_change_max_ns'):
            if order.get(key) is not None and type(order[key]) is not int:
                raise CaptureError('invalid_capture_order')
        expected = self.stamp(at, order['monotonic_before_ns'], order['monotonic_ns'])
        if order != expected:raise CaptureError('capture_order_mismatch')

    def verify(self, frame):
        self.verify_order(frame['at'], frame.get('capture_order'))


def ordered_frame(raw):
    def decode(value):
        if set(value) == {'@datetime'}:
            return datetime.fromisoformat(value['@datetime'])
        return value
    try:
        frame = json.loads(raw, object_hook=decode)
        datetime_ns(frame['at'])
        return frame
    except (KeyError, TypeError, ValueError) as exc:
        raise CaptureError('invalid_ordered_frame') from exc


class DirectoryBudget:
    """One recording writer per directory; retain old runs, never auto-delete."""
    def __init__(self, root, limit):
        self.root, self.limit = root, limit
        self.handle = (root/'.capture.lock').open('a+b')
        if self.handle.tell() == 0:
            self.handle.write(b'0'); self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except Exception:
            self.handle.close()
            raise CaptureError('capture_directory_busy')

    def check(self, next_bytes):
        used = sum(p.stat().st_size for p in self.root.iterdir() if p.is_file())
        # The manifest and its DELETE journal need bounded space to record failure.
        reserve = 3*MANIFEST_BYTES
        if used+next_bytes+reserve > self.limit:
            raise CaptureError('directory_storage_limit')
        if shutil.disk_usage(self.root).free < next_bytes+reserve+256*1024*1024:
            raise CaptureError('disk_free_space_floor')

    def close(self):
        self.handle.close()  # OS releases the advisory lock, including on crash.


class SegmentWriter:
    def __init__(self, path, metadata, dumps, *, max_bytes=DEFAULT_DATA_BYTES,
                 segment_bytes=DEFAULT_SEGMENT_BYTES, directory_bytes=8*1024*1024*1024):
        self.path = Path(path)
        self.dumps = dumps
        self.max_bytes = max_bytes
        self.segment_bytes = min(segment_bytes, max_bytes)
        self.db = self.file = None
        self.current = None
        self.total_bytes = self.frames = 0
        self.first = self.last = None
        self.maximum_gap_seconds = 0.
        schema = metadata.get('clock_schema')
        if schema not in (None, CLOCK_SCHEMA):
            raise CaptureError('unsupported_capture_clock')
        self.order = CaptureOrder() if schema else None
        self.meta = dict(metadata, capture_layout=LAYOUT, closed=False, error=None,
                         segments=[], frames=0, dropped=0,
                         storage_policy=dict(segment_bytes=self.segment_bytes,
                             data_bytes=max_bytes, manifest_bytes=MANIFEST_BYTES,
                             transient_manifest_journal_bytes=2*MANIFEST_BYTES,
                             max_frame_bytes=MAX_FRAME_BYTES, max_segments=MAX_SEGMENTS))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.budget = DirectoryBudget(self.path.parent,directory_bytes)
        self.meta['storage_policy'].update(directory_bytes=directory_bytes,
                                          minimum_free_bytes=256*1024*1024)
        try:
            self.budget.check(0)
            # Never attach to or overwrite an earlier run, even if interrupted.
            with self.path.open('xb'):
                pass
            self.db = sqlite3.connect(self.path)
            self.db.execute('PRAGMA page_size=4096')
            self.db.execute('PRAGMA max_page_count=%d' % (MANIFEST_BYTES//4096))
            self.db.execute('PRAGMA journal_mode=DELETE')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('CREATE TABLE metadata(id INTEGER PRIMARY KEY,body TEXT NOT NULL)')
            self.db.execute('CREATE TABLE frames(seq INTEGER PRIMARY KEY,body BLOB NOT NULL)')
            self.db.execute('INSERT INTO metadata VALUES (1,?)', (self.dumps(self.meta),))
            self.db.commit()
        except Exception:
            if self.db is not None:self.db.close()
            self.budget.close()
            raise

    def checkpoint(self, **updates):
        if self.file is not None:
            self.file.flush()
            os.fsync(self.file.fileno())
        self.meta.update(updates, frames=self.frames, compressed_record_bytes=self.total_bytes,
                         first_at=self.first, last_at=self.last,
                         max_interframe_gap_seconds=self.maximum_gap_seconds)
        if self.order is not None:
            self.meta.update(clock_discontinuities=self.order.discontinuities,
                             clock_replay_eligible=self.order.replay_eligible,
                             clock_uncertain_samples=self.order.uncertain_samples,
                             raw_wall_regressions=self.order.wall_regressions,
                             max_monotonic_gap_seconds=self.order.maximum_gap_seconds)
        body = self.dumps(self.meta)
        # Reserve room for database pages and subsequent failure diagnostics.
        if len(body.encode('utf-8')) > MANIFEST_BYTES//2:
            raise CaptureError('manifest_metadata_limit')
        self.db.execute('UPDATE metadata SET body=? WHERE id=1',(body,))
        self.db.commit()

    def _seal(self):
        if self.file is None:return
        self.file.flush()
        os.fsync(self.file.fileno())
        self.file.close()
        self.file = None
        self.current['sealed'] = True
        self.current['sha256'] = self.digest.hexdigest()

    def append(self, raw, at, capture_order=None):
        if (not isinstance(at,datetime) or at.tzinfo is None
                or at.astimezone(timezone(timedelta(hours=5,minutes=30))).date().isoformat()!=self.meta.get('day')):
            raise CaptureError('invalid_capture_session_time')
        if len(raw) > MAX_FRAME_BYTES:raise CaptureError('frame_size_limit')
        body = zlib.compress(raw,level=1)
        length = HEADER.size+len(body)
        if length > self.segment_bytes:raise CaptureError('frame_exceeds_segment_limit')
        if self.total_bytes+length > self.max_bytes:raise CaptureError('session_storage_limit')
        self.budget.check(length)
        if self.order is not None:
            if capture_order is None:
                frame = ordered_frame(raw)
                if frame['at'] != at:raise CaptureError('capture_wall_time_mismatch')
                capture_order = frame.get('capture_order')
            # Commit order state only after the record was written successfully.
            import copy
            next_order = copy.copy(self.order)
            next_order.verify_order(at, capture_order)
        if self.last is not None:
            delta = (at-self.last).total_seconds()
            if delta < 0 and self.order is None:raise CaptureError('nonmonotonic_capture_time')
            self.maximum_gap_seconds=max(self.maximum_gap_seconds,delta)
        if self.file is None or self.current['bytes']+length > self.segment_bytes:
            self._seal()
            if len(self.meta['segments']) >= MAX_SEGMENTS:raise CaptureError('segment_count_limit')
            name = self.path.stem+'.part%04d.bin' % (len(self.meta['segments'])+1)
            # Exclusive creation prevents overwriting orphaned data after a crash.
            self.file = (self.path.parent/name).open('xb',buffering=0)
            self.digest = hashlib.sha256()
            self.current = dict(name=name,bytes=0,frames=0,start_seq=self.frames+1,
                                end_seq=self.frames,sealed=False,sha256=None)
            self.meta['segments'].append(self.current)
            self.checkpoint()
        record = HEADER.pack(self.frames+1,len(body))+body
        written = self.file.write(record)
        if written != len(record):raise CaptureError('partial_segment_write')
        self.digest.update(record)
        if self.order is not None:self.order = next_order
        self.frames += 1
        self.total_bytes += len(record)
        self.current.update(bytes=self.current['bytes']+len(record),
                            frames=self.current['frames']+1,end_seq=self.frames)
        self.first = self.first or at
        self.last = at
        if self.frames % 100 == 0:self.checkpoint()

    def finish(self, **updates):
        self._seal()
        self.checkpoint(closed=True, **updates)

    def close(self):
        if self.file is not None:self.file.close()
        if self.db is not None:self.db.close()
        self.budget.close()


def segment_paths(path, meta):
    root = Path(path).resolve().parent
    entries = meta.get('segments')
    if not isinstance(entries,list) or len(entries)>MAX_SEGMENTS:raise CaptureError('invalid_segment_manifest')
    names=set()
    for entry in entries:
        name=entry.get('name')
        if (not isinstance(name,str) or Path(name).name!=name or '/' in name or '\\' in name
                or ':' in name or name in names):raise CaptureError('unsafe_segment_name')
        target=(root/name).resolve()
        if target.parent!=root:raise CaptureError('segment_outside_capture_directory')
        names.add(name)
        yield target,entry


def validated_records(path,meta):
    """Closed complete captures only. Validate each segment before yielding it.
    A corrupt later segment raises; callers must not publish partial results.
    """
    if not meta.get('closed') or meta.get('error') or meta.get('dropped'):
        raise CaptureError('incomplete_segmented_capture')
    schema = meta.get('clock_schema')
    if schema not in (None, CLOCK_SCHEMA):raise CaptureError('unsupported_capture_clock')
    order = CaptureOrder() if schema else None
    expected=1
    total=0
    for target,entry in segment_paths(path,meta):
        if not entry.get('sealed') or entry.get('start_seq')!=expected:
            raise CaptureError('unsealed_or_missing_sequence')
        length=entry.get('bytes')
        if not isinstance(length,int) or not 0<=length<=meta['storage_policy']['segment_bytes']:
            raise CaptureError('invalid_segment_size')
        with target.open('rb') as f:
            if os.fstat(f.fileno()).st_size != length:raise CaptureError('segment_length_mismatch')
            digest=hashlib.sha256()
            while block:=f.read(1024*1024):digest.update(block)
            if digest.hexdigest()!=entry.get('sha256'):raise CaptureError('segment_checksum_mismatch')
            f.seek(0);count=0
            while header:=f.read(HEADER.size):
                if len(header)!=HEADER.size:raise CaptureError('truncated_frame_header')
                seq,size=HEADER.unpack(header)
                if seq!=expected or size>MAX_FRAME_BYTES+65536:raise CaptureError('invalid_frame_header')
                body=f.read(size)
                if len(body)!=size:raise CaptureError('truncated_frame_body')
                decoder=zlib.decompressobj()
                raw=decoder.decompress(body,MAX_FRAME_BYTES+1)
                if len(raw)>MAX_FRAME_BYTES or not decoder.eof or decoder.unused_data:
                    raise CaptureError('invalid_compressed_frame')
                if order is not None:
                    frame = ordered_frame(raw)
                    if frame['at'].astimezone(timezone(timedelta(hours=5,minutes=30))).date().isoformat() != meta.get('day'):
                        raise CaptureError('invalid_capture_session_time')
                    order.verify(frame)
                expected+=1;count+=1
                yield raw
            if count!=entry.get('frames') or expected-1!=entry.get('end_seq'):
                raise CaptureError('segment_frame_count_mismatch')
        total+=length
    if expected-1!=meta.get('frames') or total!=meta.get('compressed_record_bytes'):
        raise CaptureError('capture_totals_mismatch')

    if order is not None and (meta.get('clock_discontinuities') != order.discontinuities
            or meta.get('clock_replay_eligible') is not order.replay_eligible
            or meta.get('clock_uncertain_samples') != order.uncertain_samples
            or meta.get('raw_wall_regressions') != order.wall_regressions
            or meta.get('max_monotonic_gap_seconds') != order.maximum_gap_seconds):
        raise CaptureError('capture_clock_summary_mismatch')
