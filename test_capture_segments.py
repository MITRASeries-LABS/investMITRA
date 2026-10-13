"""Bounded segmented capture, corruption and legacy replay tests; no services."""
import copy
from datetime import datetime,timedelta
import hashlib
import json
from pathlib import Path
import queue
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import zlib
import sys
sys.path.insert(0,str(Path(__file__).parent/'scripts'))
from comparison_capture import ComparisonTape,read_metadata,read_frames,dumps,decode
from capture_segments import SegmentWriter,CaptureError,HEADER,MAX_FRAME_BYTES,MANIFEST_BYTES
from datetime import timezone
IST = timezone(timedelta(hours=5, minutes=30))

class SegmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'tape.sqlite3'
        self.now=datetime(2026,10,8,10,tzinfo=IST)
        self.meta=dict(version='execution-compare-v1',day='2026-10-08')
    def frame(self,i=0):return dict(at=self.now+timedelta(seconds=i),ticks=[],symbols={},context={},pad='fixture'+str(i))
    def raw(self,i=0):return dumps(self.frame(i)).encode()
    def writer(self,**kwargs):
        w=SegmentWriter(self.path,self.meta,dumps,**kwargs);self.addCleanup(w.close);return w
    def rewrite_meta(self,meta):
        db=sqlite3.connect(self.path);db.execute('update metadata set body=?',(dumps(meta),));db.commit();db.close()
    def test_exact_boundary_rotation_and_reopen(self):
        raw=self.raw();size=HEADER.size+len(zlib.compress(raw,1))
        w=self.writer(segment_bytes=size,max_bytes=size*3)
        for _ in range(3):w.append(raw,self.now)
        w.finish(error=None,dropped=0);w.close()
        m=read_metadata(self.path)
        self.assertEqual(len(m['segments']),3)
        self.assertTrue(all(s['bytes']==size for s in m['segments']))
        self.assertEqual(len(list(read_frames(self.path))),3)
        self.assertEqual(len(list(read_frames(self.path))),3)
    def test_quota_prevents_overshoot(self):
        raw=self.raw();size=HEADER.size+len(zlib.compress(raw,1));w=self.writer(segment_bytes=size,max_bytes=size*2-1)
        w.append(raw,self.now)
        with self.assertRaisesRegex(CaptureError,'session_storage_limit'):w.append(raw,self.now)
        w.finish(error='CaptureError',error_detail='session_storage_limit',dropped=0)
        self.assertEqual(sum(p.stat().st_size for p in self.root.glob('*.bin')),size)
        with self.assertRaises(CaptureError):list(read_frames(self.path))
    def test_oversized_single_frame_rejected_before_segment_creation(self):
        w=self.writer(segment_bytes=10)
        with self.assertRaisesRegex(CaptureError,'frame_exceeds_segment_limit'):w.append(self.raw(),self.now)
        self.assertEqual(list(self.root.glob('*.bin')),[])
        with self.assertRaisesRegex(CaptureError,'frame_size_limit'):w.append(b'x'*(MAX_FRAME_BYTES+1),self.now)
    def test_directory_budget_retains_old_files(self):
        prior=self.root/'old.bin';prior.write_bytes(b'x'*100)
        with self.assertRaisesRegex(CaptureError,'directory_storage_limit'):
            self.writer(directory_bytes=3*MANIFEST_BYTES)
        self.assertEqual(prior.read_bytes(),b'x'*100)
    def test_free_space_floor_stops_before_data_write(self):
        w=self.writer()
        with patch('capture_segments.shutil.disk_usage',return_value=type('Disk',(),{'free':0})()):
            with self.assertRaisesRegex(CaptureError,'disk_free_space_floor'):w.append(self.raw(),self.now)
        self.assertEqual(list(self.root.glob('*.bin')),[])
    def test_same_directory_concurrent_writer_refused(self):
        w=self.writer()
        with self.assertRaisesRegex(CaptureError,'capture_directory_busy'):
            SegmentWriter(self.root/'other.sqlite3',self.meta,dumps)
        w.close()
        other=SegmentWriter(self.root/'other.sqlite3',self.meta,dumps);other.close()
    def test_existing_manifest_never_overwritten_even_after_interruption(self):
        w=self.writer();w.append(self.raw(),self.now);w.checkpoint();w.close()
        before=self.path.read_bytes()
        with self.assertRaises(FileExistsError):SegmentWriter(self.path,self.meta,dumps)
        self.assertEqual(before,self.path.read_bytes())
        with self.assertRaisesRegex(CaptureError,'incomplete'):list(read_frames(self.path))
    def sealed(self):
        w=self.writer(segment_bytes=200)
        for i in range(4):w.append(self.raw(i),self.now+timedelta(seconds=i))
        w.finish(error=None,dropped=0);w.close();return read_metadata(self.path)
    def test_missing_middle_segment_fails(self):
        m=self.sealed();(self.root/m['segments'][1]['name']).unlink()
        with self.assertRaises(FileNotFoundError):list(read_frames(self.path))
    def test_corruption_and_trailing_bytes_fail(self):
        m=self.sealed();p=self.root/m['segments'][0]['name'];raw=p.read_bytes()
        p.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
        with self.assertRaisesRegex(CaptureError,'checksum'):list(read_frames(self.path))
        p.write_bytes(raw+b'x')
        with self.assertRaisesRegex(CaptureError,'length'):list(read_frames(self.path))
    def test_sequence_gap_and_unsafe_path_fail(self):
        m=self.sealed();original=copy.deepcopy(m)
        m['segments'][1]['start_seq']+=1;self.rewrite_meta(m)
        with self.assertRaisesRegex(CaptureError,'sequence'):list(read_frames(self.path))
        original['segments'][0]['name']='../outside.bin';self.rewrite_meta(original)
        with self.assertRaisesRegex(CaptureError,'unsafe'):list(read_frames(self.path))
    def test_nonmonotonic_and_wrong_session_rejected(self):
        w=self.writer();w.append(self.raw(),self.now)
        for at in [self.now-timedelta(seconds=1),self.now+timedelta(days=1),self.now.replace(tzinfo=None)]:
            with self.assertRaises(CaptureError):w.append(self.raw(),at)
    def test_legacy_sqlite_still_reads(self):
        db=sqlite3.connect(self.path)
        db.execute('create table metadata(id integer,body text)');db.execute('create table frames(seq integer,body blob)')
        db.execute('insert into metadata values(1,?)',(dumps(self.meta),));db.execute('insert into frames values(1,?)',(zlib.compress(self.raw()),));db.commit();db.close()
        self.assertEqual(list(read_frames(self.path)),[self.frame()])
    def test_worker_error_has_specific_cause_and_last_good_time(self):
        tape=ComparisonTape(self.path,self.meta,lambda:{},max_bytes=200,segment_bytes=200)
        for i in range(5):tape.queue.put(self.frame(i))
        tape.close();m=read_metadata(self.path)
        self.assertEqual(m['error_detail'],'session_storage_limit')
        self.assertEqual(m['last_at'],self.now)
        self.assertEqual(m['recording_stopped_at'],self.now+timedelta(seconds=1))
        self.assertTrue(m['queued_not_written'])
    def test_byte_backpressure_marks_explicit_loss(self):
        gate=threading.Event()
        def loader():gate.wait(2);return {}
        tape=ComparisonTape(self.path,self.meta,loader,queue_bytes=1)
        with patch('comparison_capture.capture_frame',return_value=self.frame()):tape.capture(None,[],self.now)
        gate.set();tape.close();m=read_metadata(self.path)
        self.assertEqual(m['loss_counts'],{'queue_byte_limit':1})
        self.assertEqual(m['first_lost_at'],self.now)
        with self.assertRaises(CaptureError):list(read_frames(self.path))
    def test_worker_roundtrip_multiple_segments_and_gap_metadata(self):
        tape=ComparisonTape(self.path,self.meta,lambda:{},segment_bytes=200)
        for i in (0,1,200):tape.queue.put(self.frame(i))
        tape.close();m=read_metadata(self.path)
        self.assertIsNone(m['error']);self.assertEqual(m['max_interframe_gap_seconds'],199)
        self.assertEqual(len(list(read_frames(self.path))),3)
    def test_unbounded_queue_arguments_refused(self):
        with self.assertRaises(ValueError):ComparisonTape(self.path,self.meta,lambda:{},queue_size=0)

    def test_partial_write_is_preserved_and_never_complete(self):
        w=self.writer();w.append(self.raw(),self.now)
        real=w.file
        class Partial:
            def write(self,data):return real.write(data[:len(data)//2])
            def flush(self):return real.flush()
            def fileno(self):return real.fileno()
            def close(self):return real.close()
        w.file=Partial()
        with self.assertRaisesRegex(CaptureError,'partial_segment_write'):
            w.append(self.raw(1),self.now+timedelta(seconds=1))
        w.finish(error='CaptureError',error_detail='partial_segment_write',dropped=0)
        self.assertEqual(read_metadata(self.path)['frames'],1)
        with self.assertRaises(CaptureError):list(read_frames(self.path))

    def test_close_timeout_does_not_publish_complete_recording(self):
        entered=threading.Event();release=threading.Event()
        def loader():entered.set();release.wait(3);return {}
        tape=ComparisonTape(self.path,self.meta,loader)
        self.assertTrue(entered.wait(2))
        with patch.object(tape.worker,'join',return_value=None):tape.close()
        self.assertFalse(read_metadata(self.path)['closed'])
        release.set();tape.close()
        self.assertFalse(tape.worker.is_alive())

    def test_finalization_error_leaves_capture_incomplete(self):
        with patch('capture_segments.SegmentWriter.finish',side_effect=OSError('fixture disk failure')):
            tape=ComparisonTape(self.path,self.meta,lambda:{})
            tape.queue.put(self.frame());tape.close()
        self.assertEqual(tape.failed,'OSError')
        self.assertFalse(read_metadata(self.path)['closed'])
        with self.assertRaises(CaptureError):list(read_frames(self.path))

    def test_decompression_bomb_rejected_even_with_matching_checksum(self):
        m=self.sealed();entry=m['segments'][0];path=self.root/entry['name']
        body=zlib.compress(b'x'*(MAX_FRAME_BYTES+1))
        raw=HEADER.pack(1,len(body))+body;path.write_bytes(raw)
        entry.update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        m['storage_policy']['segment_bytes']=len(raw)
        self.rewrite_meta(m)
        with self.assertRaisesRegex(CaptureError,'compressed_frame'):list(read_frames(self.path))

if __name__=='__main__':unittest.main()
