"""Recorder-only clock-step regressions; no broker, network or runtime imports."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import itertools
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zlib
sys.path.insert(0, str(Path(__file__).parent / 'scripts'))
from capture_segments import CLOCK_SCHEMA, CaptureOrder, CaptureError, SegmentWriter, HEADER
from comparison_capture import ComparisonTape, dumps, read_metadata, read_frames

IST = timezone(timedelta(hours=5, minutes=30))

class ClockCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'tape.sqlite3'
        self.at = datetime.fromisoformat('2026-10-13T10:55:07.794395+05:30')
        self.meta = dict(day='2026-10-13', version='execution-compare-v1', clock_schema=CLOCK_SCHEMA)

    def frames(self, offsets, elapsed=None):
        order = CaptureOrder()
        if elapsed is None: elapsed = [i * 1_000_000_000 for i in range(len(offsets))]
        result = []
        for offset, ns in zip(offsets, elapsed):
            at = self.at + timedelta(seconds=offset)
            result.append(dict(at=at, ticks=[{'exchange_timestamp': self.at, 'last_price': 100}],
                               symbols={}, context={}, capture_order=order.stamp(at, ns, ns)))
        return result

    def save(self, frames, **kwargs):
        writer = SegmentWriter(self.path, self.meta, dumps, **kwargs)
        try:
            for frame in frames:writer.append(dumps(frame).encode(), frame['at'])
            writer.finish()
        finally:writer.close()
        return read_metadata(self.path)

    def test_exact_windows_rollback_preserves_raw_times_and_next_observation(self):
        # Old 10:55:08.0151422 -> new 10:55:06.7087475: -1.3063947 seconds.
        frames = self.frames([0, -1.058387, -.058387], [0, 248_007_700, 1_248_007_700])
        meta = self.save(frames)
        self.assertEqual(list(read_frames(self.path)), frames)
        step = frames[1]['capture_order']
        self.assertEqual((step['offset_change_min_ns'] + step['offset_change_max_ns']) // 2, -1_306_394_700)
        self.assertEqual(meta['clock_discontinuities'], 1)
        self.assertFalse(meta['clock_replay_eligible'])
        self.assertEqual(meta['frames'], 3)
        self.assertEqual(meta['max_monotonic_gap_seconds'], 1)
        self.assertEqual(frames[1]['at'].isoformat(), '2026-10-13T10:55:06.736008+05:30')

    def test_forward_step_flags_without_fabricating_gap(self):
        meta = self.save(self.frames([0, 121, 122]))
        self.assertEqual(meta['clock_discontinuities'], 1)
        self.assertEqual(meta['max_monotonic_gap_seconds'], 1)
        self.assertEqual(meta['max_interframe_gap_seconds'], 121)

    def test_real_receipt_gap_does_not_become_clock_step(self):
        meta = self.save(self.frames([0, 121], [0, 121_000_000_000]))
        self.assertTrue(meta['clock_replay_eligible'])
        self.assertEqual(meta['max_monotonic_gap_seconds'], 121)

    def test_equal_wall_timestamps_supported(self):
        meta = self.save(self.frames([0, 0], [0, 1000]))
        self.assertTrue(meta['clock_replay_eligible'])

    def test_corrupt_sequence_monotonic_and_annotation_rejected(self):
        for field, value in [('seq', 4), ('monotonic_ns', 0), ('clock_discontinuity', False),
                             ('wall_delta_ns', True)]:
            order = CaptureOrder();frames = self.frames([0, -1])
            order.verify(frames[0]); frames[1]['capture_order'][field] = value
            with self.assertRaises(CaptureError): order.verify(frames[1])

    def test_segment_rollover_retains_order_and_discontinuity(self):
        frames = self.frames([0, -1, 0, 1])
        size = max(HEADER.size + len(zlib.compress(dumps(f).encode(), 1)) for f in frames)
        meta = self.save(frames, segment_bytes=size)
        self.assertEqual(len(meta['segments']), 4)
        self.assertEqual(list(read_frames(self.path)), frames)

    def test_reader_rejects_forged_annotation_even_with_updated_checksum(self):
        frames = self.frames([0, 1]);meta = self.save(frames)
        frames[1]['capture_order']['seq'] = 9
        body = b''
        for i, frame in enumerate(frames, 1):
            compressed = zlib.compress(dumps(frame).encode(), 1)
            body += HEADER.pack(i, len(compressed)) + compressed
        entry = meta['segments'][0];(self.path.parent / entry['name']).write_bytes(body)
        entry.update(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
        meta['compressed_record_bytes'] = len(body)
        self.rewrite_meta(meta)
        with self.assertRaisesRegex(CaptureError, 'capture_order_mismatch'):list(read_frames(self.path))

    def rewrite_meta(self, meta):
        db = sqlite3.connect(self.path)
        try:
            db.execute('update metadata set body=?', (dumps(meta),));db.commit()
        finally:db.close()

    def test_reader_rejects_false_clock_summary(self):
        meta = self.save(self.frames([0, -1]))
        meta.update(clock_discontinuities=0, clock_replay_eligible=True)
        self.rewrite_meta(meta)
        with self.assertRaisesRegex(CaptureError, 'capture_clock_summary_mismatch'):list(read_frames(self.path))

    def test_unknown_clock_schema_rejected(self):
        self.meta['clock_schema'] = 'future-clock'
        with self.assertRaisesRegex(CaptureError, 'unsupported_capture_clock'):
            SegmentWriter(self.path, self.meta, dumps)

    def test_concurrent_callbacks_serialized_and_worker_continues_after_step(self):
        tape = ComparisonTape(self.path, self.meta, lambda: {}, queue_size=128)
        counter = itertools.count()
        engine_lock = threading.RLock()
        errors = []
        def frame(engine, ticks, now):
            return dict(at=now, ticks=ticks, symbols={}, context={})
        def capture(i):
            try:
                with engine_lock:
                    ns = next(counter)*1000
                    tape.capture(None, [{'instrument_token': i}], self.at, clock_sample=(ns,ns+1))
            except Exception as exc:errors.append(exc)
        with patch('comparison_capture.capture_frame', side_effect=frame):
            threads = [threading.Thread(target=capture, args=(i,)) for i in range(24)]
            for thread in threads:thread.start()
            for thread in threads:thread.join()
            ns=next(counter)*1000
            tape.capture(None, [], self.at-timedelta(seconds=1.306), clock_sample=(ns,ns+1))
            ns=next(counter)*1000
            tape.capture(None, [], self.at, clock_sample=(ns,ns+1))
        tape.close()
        self.assertFalse(errors)
        self.assertIsNone(tape.failed)
        frames = list(read_frames(self.path))
        self.assertEqual([f['capture_order']['seq'] for f in frames], list(range(1, 27)))
        self.assertEqual({f['ticks'][0]['instrument_token'] for f in frames[:24]}, set(range(24)))
        self.assertEqual(read_metadata(self.path)['clock_discontinuities'], 2)

    def test_replay_accepts_clean_clock_metadata_and_rejects_steps(self):
        from strategy_comparison import validate_tape, source_hashes
        meta = self.save(self.frames([0, 1]))
        meta.update(source_hashes=source_hashes(), initial_execution_flat=True, initial_execution_trades=0)
        validate_tape(meta)
        meta.update(clock_discontinuities=1, clock_replay_eligible=False)
        with self.assertRaisesRegex(ValueError, 'Clock discontinuity'):validate_tape(meta)
        meta.pop('clock_schema');meta.pop('clock_discontinuities');meta.pop('clock_replay_eligible')
        validate_tape(meta)  # Legacy capture validation remains supported.

    def test_full_runner_clean_ordered_and_legacy_outputs_agree(self):
        from test_strategy_comparison import ComparisonTests, engine_module
        from strategy_comparison import run
        fixture = ComparisonTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        frames = [fixture.frame(), fixture.frame(fixture.now + timedelta(seconds=5))]
        outputs = []
        for schema in (None, CLOCK_SCHEMA):
            meta = dict(fixture.meta)
            if schema:meta['clock_schema'] = schema
            path = self.path.parent / ('ordered.sqlite3' if schema else 'legacy.sqlite3')
            writer = SegmentWriter(path, meta, dumps)
            order = CaptureOrder()
            try:
                for i, original in enumerate(frames):
                    frame = copy.deepcopy(original)
                    if schema:frame['capture_order'] = order.stamp(frame['at'], i * 5_000_000_000, i * 5_000_000_000)
                    writer.append(dumps(frame).encode(), frame['at'])
                writer.finish()
            finally:writer.close()
            outputs.append(run(path, engine_module))
        self.assertEqual(outputs[0], outputs[1])

    def test_full_runner_rejects_step_before_advancing_strategy(self):
        from strategy_comparison import run
        meta = self.save(self.frames([0, 121]))
        with patch('strategy_comparison.PortfolioRun', side_effect=AssertionError('strategy must not run')):
            with self.assertRaisesRegex(ValueError, 'Clock discontinuity'):run(self.path)

    def test_251ms_callback_delay_after_paired_sample_is_not_clock_step(self):
        # A caller can wait arbitrarily after its pair; the recorder uses that pair,
        # never a later timestamp that would turn delivery delay into clock evidence.
        tape = ComparisonTape(self.path, self.meta, lambda: {})
        frames = self.frames([0, 1, 2])
        with patch('comparison_capture.capture_frame', side_effect=lambda e,t,at: dict(at=at,ticks=t,symbols={},context={})), patch('time.monotonic_ns', side_effect=AssertionError('recorder must not resample')):
            for i, frame in enumerate(frames):
                tape.capture(None, [], frame['at'], clock_sample=(i*1_000_000_000,i*1_000_000_000+1000))
        tape.close()
        meta=read_metadata(self.path)
        self.assertIsNone(tape.failed)
        self.assertEqual(meta['clock_discontinuities'],0)
        self.assertTrue(meta['clock_replay_eligible'])

    def test_251ms_stall_inside_bracket_is_uncertainty_not_clock_step(self):
        order=CaptureOrder()
        samples=[(0,0,1000),(1_000_000_000,1_000_000_000,1_251_000_000),(2_000_000_000,2_000_000_000,2_000_001_000)]
        stamps=[order.stamp(self.at+timedelta(microseconds=wall//1000),before,after) for wall,before,after in samples]
        self.assertFalse(any(x['clock_discontinuity'] for x in stamps))
        self.assertEqual(order.uncertain_samples,1)
        self.assertFalse(order.replay_eligible)

    def test_variable_pair_sampling_jitter_has_no_false_step(self):
        order=CaptureOrder()
        for i, (before_ms,after_ms) in enumerate([(0,1),(100,5),(2,120),(0,0),(40,80)]):
            wall_ns=(i+1)*1_000_000_000
            stamp=order.stamp(self.at+timedelta(seconds=i+1),wall_ns-before_ms*1_000_000,wall_ns+after_ms*1_000_000)
            self.assertFalse(stamp['clock_discontinuity'])
        self.assertTrue(order.replay_eligible)

    def test_step_overlapping_wide_bracket_remains_uncertain(self):
        order=CaptureOrder()
        order.stamp(self.at,0,1000)
        stamp=order.stamp(self.at+timedelta(seconds=1),1_000_000_000,2_500_000_000)
        self.assertFalse(stamp['clock_discontinuity'])
        self.assertTrue(stamp['sampling_uncertain'])
        self.assertFalse(order.replay_eligible)

    def test_missing_engine_clock_pair_fails_closed(self):
        tape=ComparisonTape(self.path,self.meta,lambda:{})
        tape.capture(None,[],self.at)
        tape.close()
        self.assertEqual(tape.failed,'CaptureError')
        with self.assertRaises(CaptureError):list(read_frames(self.path))

    def test_fast_writer_reader_binds_companion_order_to_payload(self):
        frames=self.frames([0,1]);writer=SegmentWriter(self.path,self.meta,dumps)
        try:
            for frame in frames:
                raw=copy.deepcopy(frame)
                raw['capture_order']['seq']+=10
                writer.append(dumps(raw).encode(),frame['at'],capture_order=frame['capture_order'])
            writer.finish()
        finally:writer.close()
        with self.assertRaisesRegex(CaptureError,'capture_order_mismatch'):list(read_frames(self.path))

    def test_engine_brackets_exact_existing_wall_read_once(self):
        from test_strategy_comparison import ComparisonTests, engine_module
        from types import SimpleNamespace
        fixture=ComparisonTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        engine=fixture.engine;events=[];captured=[]
        def wall():events.append('wall');return fixture.now
        ns=iter([1000,2000])
        def mono():events.append('mono');return next(ns)
        def capture(e,t,at,*,clock_sample):
            events.append('capture');captured.append((at,clock_sample))
        engine._now=wall;engine.comparison_tape=SimpleNamespace(capture=capture)
        with patch.object(engine_module.time,'monotonic_ns',side_effect=mono):
            engine._on_tick_locked(None,[])
        self.assertEqual(events,['mono','wall','mono','capture'])
        self.assertEqual(captured,[(fixture.now,(1000,2000))])

    def assert_drop_then_recover(self, reason):
        release=threading.Event()
        tape=ComparisonTape(self.path,self.meta,lambda: release.wait(3) or {})
        def frame(engine,ticks,at):return dict(at=at,ticks=ticks,symbols={},context={})
        try:
            with patch('comparison_capture.capture_frame',side_effect=frame):
                tape.capture(None,[],self.at,clock_sample=(0,1000))
                self.assertEqual(tape.order.seq,1)
                if reason=='queue_byte_limit':
                    tape.queue_bytes=tape.pending_bytes
                    tape.capture(None,[],self.at+timedelta(seconds=1),clock_sample=(1_000_000_000,1_000_001_000))
                    tape.queue_bytes=64*1024*1024
                else:
                    with patch('comparison_capture.MAX_FRAME_BYTES',1):
                        tape.capture(None,[],self.at+timedelta(seconds=1),clock_sample=(1_000_000_000,1_000_001_000))
                self.assertEqual(tape.order.seq,1)
                tape.capture(None,[],self.at+timedelta(seconds=2),clock_sample=(2_000_000_000,2_000_001_000))
                self.assertEqual(tape.order.seq,2)
        finally:
            release.set();tape.close()
        meta=read_metadata(self.path)
        self.assertIsNone(tape.failed)
        self.assertEqual(meta['frames'],2)
        self.assertEqual(meta['dropped'],1)
        self.assertEqual(meta['loss_counts'],{reason:1})
        self.assertEqual(meta['max_monotonic_gap_seconds'],2)
        with self.assertRaisesRegex(CaptureError,'incomplete'):list(read_frames(self.path))

    def test_queue_byte_drop_recovers_order_but_replay_stays_incomplete(self):
        self.assert_drop_then_recover('queue_byte_limit')

    def test_frame_size_drop_recovers_order_but_replay_stays_incomplete(self):
        self.assert_drop_then_recover('frame_size_limit')

if __name__ == '__main__':unittest.main()
