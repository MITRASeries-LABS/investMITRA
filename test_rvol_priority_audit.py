"""No credentials or market calls: sampled gate timing, volume arithmetic and SQL baselines."""
import contextlib
from datetime import date, datetime, timedelta
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import duckdb
import test_auto_trading  # offline scripts import path
from rvol_priority_audit import (GATES,IST,analyse,audit_volume,policy_evidence,
                                 read_records,summarise,verify_neon,compare_baselines)


class AuditTests(unittest.TestCase):
    def row(self,symbol='A',minute=0):
        at=datetime(2026,10,6,10,0,tzinfo=IST)+timedelta(minutes=minute)
        fraction=(at.hour*60+at.minute-555)/375
        expected=1000*fraction
        gates={k:dict(status='PASS') for k in GATES}
        gates['gap'].update(actual=.5)
        gates['blended'].update(actual=60)
        gates['rvol'].update(actual=10)
        gates['priority'].update(actual=3)
        return dict(symbol=symbol,day='2026-10-06',strategy_id='test',session='momentum',
            observed_at=at.timestamp(),volume=expected*10,outcome='fixture',gates=gates,
            policy=dict(rvol_min=5,priority_min=3),
            details=dict(rvol_live_volume=expected*10,rvol_avg_daily_volume=1000,
                rvol_elapsed_fraction=fraction,rvol_expected_volume=expected,rvol_uncapped=10,
                rvol_method='linear_elapsed_session_fraction',
                rvol_baseline=dict(source='NSE',first_date='2026-09-08',last_date='2026-10-05',
                                   as_of='2026-10-06',sample_days=15)))

    def test_consistent_operands_and_compounded_floor(self):
        r=self.row()
        self.assertEqual(audit_volume(r),[])
        self.assertEqual(policy_evidence(r)['approximate_joint_rvol_floor'],10)
        r['gates']['gap']['actual']=-.5
        self.assertEqual(policy_evidence(r)['approximate_joint_rvol_floor'],10)

    def test_bad_operands_missing_provenance_and_future_baseline_are_visible(self):
        r=self.row();r['details']['rvol_uncapped']=float('nan')
        self.assertIn('missing_or_nonfinite_volume_operands',audit_volume(r))
        r=self.row();r['details']['rvol_uncapped']=5
        self.assertIn('rvol_arithmetic_mismatch',audit_volume(r))
        r=self.row();r['details']['rvol_baseline']['last_date']='2026-10-06'
        self.assertIn('baseline_date_mismatch_or_lookahead',audit_volume(r))
        r=self.row();r['details']['rvol_baseline']['source']='BSE'
        self.assertIn('unverified_baseline_venue',audit_volume(r))
        r=self.row();r['volume']=1
        self.assertIn('decision_and_scoring_volume_disagree',audit_volume(r))

    def test_clock_boundary_tolerated_but_wrong_session_fraction_flagged(self):
        r=self.row();r['observed_at']+=60
        self.assertEqual(audit_volume(r),[])
        r['observed_at']+=120
        self.assertIn('elapsed_fraction_clock_mismatch',audit_volume(r))

    def test_earlier_crossing_is_not_hidden_by_later_failure_or_recounted_as_trade(self):
        early=self.row();late=self.row(minute=60);late['gates']['rvol']['status']='FAIL'
        middle=self.row(minute=1)
        a=analyse([late,middle,early])
        self.assertEqual(a['variants']['baseline']['samples'],2)
        self.assertEqual(a['variants']['baseline']['symbols'],1)
        self.assertEqual(a['variants']['baseline']['first'][0]['observed_at'],early['observed_at'])

    def test_removing_one_floor_cannot_bypass_the_other_or_unknown_sector(self):
        r=self.row();r['gates']['rvol']['status']='FAIL';r['gates']['priority']['status']='FAIL'
        unknown=self.row('B');unknown['gates']['sector']['status']='UNKNOWN'
        a=analyse([r,unknown])
        self.assertEqual(a['known'],1);self.assertEqual(a['unknown'],1)
        self.assertEqual(a['variants']['without_rvol_floor']['symbols'],0)
        self.assertEqual(a['variants']['without_priority_floor']['symbols'],0)
        self.assertEqual(a['variants']['without_rvol_and_priority']['symbols'],1)
        self.assertIn('B',a['sector_unknowns'])

    def test_removing_volume_and_priority_does_not_bypass_direction(self):
        r=self.row();r['gates']['direction_vwap']['status']='FAIL'
        self.assertEqual(analyse([r])['variants']['without_rvol_and_priority']['symbols'],0)

    def test_volume_decrease_reported_without_automatic_correction(self):
        a=self.row();b=self.row(minute=1);b['details']['rvol_live_volume']=1
        result=analyse([b,a])
        self.assertEqual(result['volume_decreases'],{'A':1})
        self.assertEqual(b['details']['rvol_live_volume'],1)

    def test_readonly_report_groups_sessions_and_does_not_create_missing_database(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'evidence.sqlite3'
            with self.assertRaises(sqlite3.OperationalError):read_records(p,'2026-10-06')
            self.assertFalse(p.exists())
            db=sqlite3.connect(p);db.execute('CREATE TABLE decisions(day TEXT,observed_at REAL,body TEXT)')
            r=self.row();a=self.row(minute=240);a['session']='afternoon'
            for item in (r,a):db.execute('INSERT INTO decisions VALUES(?,?,?)',(item['day'],item['observed_at'],json.dumps(item)))
            db.commit();db.close()
            before=hashlib.sha256(p.read_bytes()).hexdigest()
            out=io.StringIO()
            with contextlib.redirect_stdout(out):summarise(p,'2026-10-06')
            self.assertIn('test | momentum',out.getvalue());self.assertIn('test | afternoon',out.getvalue())
            self.assertEqual(before,hashlib.sha256(p.read_bytes()).hexdigest())

    def test_actual_baseline_sql_excludes_future_other_venue_and_conflicting_days(self):
        db=duckdb.connect();self.addCleanup(db.close)
        db.execute('CREATE SCHEMA investmitra')
        db.execute('CREATE TABLE investmitra.company_master(nse_symbol VARCHAR,isin VARCHAR)')
        db.execute("INSERT INTO investmitra.company_master VALUES ('A','I')")
        db.execute('CREATE TABLE investmitra.equity_prices(isin VARCHAR,source VARCHAR,trade_date DATE,volume BIGINT)')
        for day,venue,volume in [('2026-10-01','NSE',100),('2026-10-01','NSE',100),
            ('2026-10-05','NSE',300),('2026-09-30','NSE',50),('2026-09-30','NSE',60),
            ('2026-10-06','NSE',99999),('2026-10-01','BSE',99999),('2026-08-01','NSE',99999)]:
            db.execute('INSERT INTO investmitra.equity_prices VALUES(?,?,?,?)',['I',venue,day,volume])
        class Cursor:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def execute(self,sql,args):self.rows=db.execute(sql.replace('%s','?'),args).fetchall()
            def fetchall(self):return self.rows
        class Conn:
            def cursor(self):return Cursor()
        result=verify_neon(Conn(),date(2026,10,6),['A','MISSING'])
        self.assertEqual(result['A']['mean_volume'],200)
        self.assertEqual(result['A']['median_volume'],200)
        self.assertEqual(result['A']['sessions'],2)
        self.assertEqual(result['A']['largest_day_share'],.75)
        self.assertEqual(result['A']['excluded_conflicting_days'],1)
        self.assertEqual(result['MISSING']['status'],'no_valid_prior_NSE_volume')

    def test_baseline_comparison_keeps_source_revisions_distinct(self):
        r=self.row()
        self.assertEqual(compare_baselines([r],{'A':dict(mean_volume=1000)})[0]['status'],'match')
        self.assertEqual(compare_baselines([r],{'A':dict(mean_volume=500)})[0]['status'],'mismatch_or_source_revision')
        self.assertEqual(compare_baselines([r],{})[0]['status'],'unavailable')


if __name__=='__main__':unittest.main()
