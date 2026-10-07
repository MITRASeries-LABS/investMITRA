"""Native and missing NSE identities, production feature SQL and readiness coverage."""
import ast
from datetime import date, timedelta
import logging
from pathlib import Path
import tempfile
import sys
sys.path.insert(0, str(Path(__file__).parent / "scripts"))
import unittest
from unittest.mock import MagicMock, Mock, patch
import duckdb
import pandas as pd
import yaml
from market_data_contract import VERSION, canonical_price_ctes
from price_identity import identity_map, prepare_price_inputs, load_identity_rows
from signal_input_readiness import audit, summarise, require_ready

ROOT=Path(__file__).parent
DAY=date(2026,10,5)
A='INE000000001'
B='INE000000019'


class WorkflowConnections(unittest.TestCase):
    def test_feature_and_coverage_steps_receive_database_connection(self):
        workflow=yaml.safe_load((ROOT/'.github/workflows/feature_engineering.yml').read_text(encoding='utf-8'))
        job=workflow['jobs']['compute-features']
        for name in ('Compute price features', 'Validate classified NSE score coverage'):
            with self.subTest(step=name):
                step=next(s for s in job['steps'] if s.get('name')==name)
                env={**workflow.get('env',{}), **job.get('env',{}), **step.get('env',{})}
                self.assertEqual(env.get('CC_POSTGRES_URL'), '${{ secrets.CC_POSTGRES_URL }}')

    def test_overnight_caller_passes_secrets_to_feature_workflow(self):
        workflow=yaml.safe_load((ROOT/'.github/workflows/overnight_readiness.yml').read_text(encoding='utf-8'))
        scores=workflow['jobs']['scores']
        self.assertEqual(scores['uses'], './.github/workflows/feature_engineering.yml')
        self.assertEqual(scores['secrets'], 'inherit')


class Identities(unittest.TestCase):
    def test_master_ambiguity_not_last_row_wins(self):
        self.assertEqual(identity_map([(' A ',A),('a',A),('B',A),('B',B),('C','bad'),('',A)]), [('A',A)])

    def fixture(self, rows):
        db=duckdb.connect();self.addCleanup(db.close)
        db.execute('CREATE TABLE input(isin VARCHAR,nse_symbol VARCHAR,source VARCHAR,trade_date DATE,close DOUBLE,volume BIGINT,turnover_cr DOUBLE,delivery_pct DOUBLE)')
        db.executemany('INSERT INTO input VALUES (?,?,?,?,?,?,?,?)',rows)
        td=tempfile.TemporaryDirectory();self.addCleanup(td.cleanup)
        path=str(Path(td.name)/'prices.parquet')
        db.execute('COPY input TO ? (FORMAT PARQUET)',[path])
        return db,repr(path)

    def test_enrich_missing_nse_only_preserve_native_identity(self):
        db,path=self.fixture([(None,' a ','NSE',DAY,100,1000,1,50),
            ('','A','NSE',DAY,100,1000,1,50),(B,'A','NSE',DAY,110,2000,2,50),
            (None,'A','BSE',DAY,99,2,1,None),(None,'NO_MAP','NSE',DAY,100,1,1,None)])
        prepare_price_inputs(db,path,DAY,DAY,rows=[('A',A)])
        self.assertEqual(db.execute('SELECT COUNT(*) FROM feature_inputs WHERE isin=?',[A]).fetchone()[0],2)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM feature_inputs WHERE isin=?',[B]).fetchone()[0],1)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM feature_inputs WHERE isin IS NULL').fetchone()[0],2)

    def test_mapping_unavailable_fails_not_empty_success(self):
        with self.assertRaisesRegex(RuntimeError,'mapping unavailable'):
            prepare_price_inputs(Mock(),"'unused'",DAY,DAY,rows=[])

    def test_actual_feature_query_retains_today_missing_isin_and_nse_history(self):
        rows=[]
        start=date(2026,9,11)
        for i in range(25):
            day=start+timedelta(days=i)
            rows.extend([(None,'A','NSE',day,100+i,1000,1,50),
                         (A,'A','BSE',day,500+i,100,1,None)])
        db,path=self.fixture(rows)
        tree=ast.parse((ROOT/'scripts/compute_features.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='compute_price_features')
        ns=dict(pd=pd,get_duckdb_con=lambda:db,build_path=lambda _:path,timedelta=timedelta,date=date,
                canonical_price_ctes=canonical_price_ctes,PRICE_CONTRACT_VERSION=VERSION,
                logger=logging.getLogger('fixture'),
                prepare_price_inputs=lambda *args:prepare_price_inputs(*args,rows=[('A',A)]))
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'features','exec'),ns)
        result=ns['compute_price_features'](DAY)
        self.assertEqual(len(result),1); self.assertEqual(result.iloc[0]['isin'],A)
        self.assertEqual(result.iloc[0]['price'],124)
        self.assertAlmostEqual(result.iloc[0]['ret_5d_pct'],(124/119-1)*100,places=4)
        self.assertEqual(result.iloc[0]['price_contract_version'],VERSION)

    def test_master_connection_readonly_and_closed_on_failure(self):
        conn=MagicMock();conn.cursor.return_value.__enter__.return_value.execute.side_effect=RuntimeError('db failure')
        import sys,os
        with patch.dict(sys.modules,{'psycopg2':Mock(connect=Mock(return_value=conn))}),patch.dict(os.environ,{'CC_POSTGRES_URL':'fixture'}):
            with self.assertRaisesRegex(RuntimeError,'db failure'):load_identity_rows()
        conn.set_session.assert_called_once_with(readonly=True);conn.close.assert_called_once()


class Coverage(unittest.TestCase):
    def test_reported_kirlpnu_and_legacy_rows_are_explicitly_quarantined(self):
        rows=[('GOOD',VERSION,'Energy','SMALL',True,True,70,'Energy',1),
              ('KIRLPNU',None,None,'MICRO',True,False,None,'Industrials',2),
              ('KIRLPNU',VERSION,None,None,False,True,60,None,2)]
        rows += [(s,'daily-venue-v1',None,None,False,True,60,None,1)
                 for s in ('ARYAMAN','ASSAMENT','PANCHMAHQ','THACKER')]
        result=summarise(rows,DAY)
        require_ready(result)
        self.assertEqual(result['expected_classified_priced_nse_symbols'],1)
        self.assertEqual(result['eligible_classified_priced_nse_symbols'],1)
        self.assertEqual(result['quarantined_ambiguous_symbols'],['KIRLPNU'])
        self.assertEqual(len(result['wrong_contract']),4)
        self.assertEqual(result['blocking_wrong_contract'],[])
        # A bad score on a unique, classified, currently priced stock still blocks.
        rows.append(('MISSING',None,None,'SMALL',True,False,None,'Energy',1))
        self.assertFalse(summarise(rows,DAY)['ready'])

    def test_only_quarantined_rows_cannot_make_readiness_green(self):
        self.assertFalse(summarise([('A',VERSION,'Energy','SMALL',True,True,70,'Energy',2)],DAY)['ready'])
        # Missing identity evidence also fails closed.
        self.assertFalse(summarise([('A',VERSION,'Energy','SMALL',True,True,70,'Energy')],DAY)['ready'])

    def test_current_classified_old_contract_still_blocks(self):
        result=summarise([('A','daily-venue-v1','Energy','SMALL',True,True,70,'Energy',1)],DAY)
        self.assertFalse(result['ready'])
        self.assertEqual(result['blocking_wrong_contract'],['A'])

    def test_reported_344_unclassified_rows_are_not_ready(self):
        result=summarise([(str(i),VERSION,'Unknown',None,True,True,60,'Unknown',1) for i in range(344)],DAY)
        self.assertEqual(result['scored_nse_symbols'],344)
        self.assertEqual(result['eligible_classified_priced_nse_symbols'],0)
        with self.assertRaisesRegex(RuntimeError,'NOT READY'):require_ready(result)

    def test_missing_classified_score_cannot_disappear_from_denominator(self):
        rows=[('A',VERSION,'Energy','SMALL',True,True,70,'Energy',1),
              ('B',None,None,'LARGE',True,False,None,'Financial Services',1)]
        result=summarise(rows,DAY)
        self.assertEqual(result['expected_classified_priced_nse_symbols'],2)
        self.assertEqual(result['missing_classified_scores'],['B']);self.assertFalse(result['ready'])

    def test_complete_classified_universe_with_unknown_exclusions_is_ready(self):
        result=summarise([('A',VERSION,'Energy','SMALL',True,True,70,'Energy',1),
                          ('B',VERSION,'Unknown',None,True,True,50,'Unknown',1)],DAY)
        require_ready(result);self.assertEqual(result['eligible_classified_priced_nse_symbols'],1)
        self.assertEqual(result['missing_sector'],['B'])

    def test_wrong_contract_null_score_or_missing_score_sector_blocks(self):
        for version,sector,score in [('daily-venue-v1','Energy',70),(VERSION,'Energy',float('nan')),
                                    (VERSION,'Unknown',70),(VERSION,'Energy',None)]:
            result=summarise([('A',version,sector,'SMALL',True,True,score,'Energy',1)],DAY)
            self.assertFalse(result['ready'])

    def test_actual_audit_query_includes_missing_scores_and_excludes_old_prices(self):
        db=duckdb.connect();self.addCleanup(db.close)
        db.execute('CREATE SCHEMA investmitra')
        db.execute('CREATE TABLE investmitra.company_master(isin VARCHAR,nse_symbol VARCHAR,sector VARCHAR,market_cap_category VARCHAR)')
        db.execute("INSERT INTO investmitra.company_master VALUES ('1','A','Energy','SMALL'),('2','B','Energy','SMALL'),('3','OLD','Energy','SMALL')")
        db.execute('CREATE TABLE investmitra.equity_prices(isin VARCHAR,source VARCHAR,trade_date DATE,close DOUBLE)')
        db.execute("INSERT INTO investmitra.equity_prices VALUES ('1','NSE','2026-10-05',100),('2','NSE','2026-10-05',100),('3','NSE','2026-10-01',100)")
        db.execute('CREATE TABLE investmitra.daily_scores(isin VARCHAR,score_date DATE,sector VARCHAR,investmitra_score DOUBLE,price_contract_version VARCHAR)')
        db.execute("INSERT INTO investmitra.daily_scores VALUES ('1','2026-10-05','Energy',70,?)",[VERSION])
        tree=ast.parse((ROOT/'scripts/signal_input_readiness.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='audit')
        sql=next(n.args[0].value for n in ast.walk(fn) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='execute')
        rows=db.execute(sql.replace('%s','?'),[DAY,DAY]).fetchall()
        result=summarise(rows,DAY)
        self.assertEqual(result['expected_classified_priced_nse_symbols'],2)
        self.assertEqual(result['missing_classified_scores'],['B'])
        db.execute("INSERT INTO investmitra.company_master VALUES ('4',' b ','Energy','SMALL')")
        rows=db.execute(sql.replace('%s','?'),[DAY,DAY]).fetchall()
        result=summarise(rows,DAY)
        self.assertTrue(result['ready'])
        self.assertEqual(result['quarantined_ambiguous_symbols'],['B'])


class EntryIdentity(unittest.TestCase):
    def test_engine_parameterized_sql_has_no_bare_percent_characters(self):
        # psycopg2 parses placeholders before PostgreSQL sees SQL comments.
        # DuckDB query fixtures alone do not exercise that adaptation layer.
        import re
        tree=ast.parse((ROOT/'scripts/intraday_signals.py').read_text(encoding='utf-8'))
        checked=0
        for node in ast.walk(tree):
            if not (isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
                    and node.func.attr in ('execute','executemany') and len(node.args)>1
                    and isinstance(node.args[0],ast.Constant)
                    and isinstance(node.args[0].value,str)):
                continue
            checked+=1
            sql=node.args[0].value
            # Consume valid placeholders and escaped literal percents first.
            remainder=re.sub(r'%%|%s|%\([^)]+\)s','',sql)
            with self.subTest(line=node.lineno):self.assertNotIn('%',remainder)
        self.assertGreater(checked,0)

    def test_laptop_revalidation_loads_env_before_database_and_lake_checks(self):
        import pipeline_readiness as readiness
        from datetime import datetime
        from pipeline_date import IST
        from types import SimpleNamespace
        now=datetime(2026,10,6,5,tzinfo=IST)
        events=[]
        dotenv=Mock(load_dotenv=Mock(side_effect=lambda *args:events.append('env')))
        with patch.dict(sys.modules,{'dotenv':dotenv}), patch.object(sys,'argv',
                ['readiness','--date','2026-10-05','--mark-ready','--check-only']), \
             patch.object(readiness,'datetime',SimpleNamespace(now=lambda tz:now)), \
             patch('signal_runtime.load_nse_holidays',return_value={date(2026,10,2)}), \
             patch.object(readiness,'inspect',side_effect=lambda *args,**kwargs:(events.append('inspect') or (True,[]))), \
             patch.object(readiness,'output'):
            readiness.main()
        self.assertEqual(events,['env','inspect'])
        dotenv.load_dotenv.assert_called_once_with('.env.prod')

    def test_production_catalog_excludes_ambiguity_unpriced_and_legacy_scores(self):
        db=duckdb.connect();self.addCleanup(db.close)
        db.execute('CREATE SCHEMA investmitra')
        db.execute('CREATE TABLE investmitra.company_master(isin VARCHAR,nse_symbol VARCHAR,sector VARCHAR,market_cap_category VARCHAR)')
        db.execute('CREATE TABLE investmitra.equity_prices(isin VARCHAR,trade_date DATE,source VARCHAR,close DOUBLE,volume BIGINT)')
        db.execute('CREATE TABLE investmitra.daily_scores(isin VARCHAR,score_date DATE,company_name VARCHAR,sector VARCHAR,investmitra_score DOUBLE,signal VARCHAR,momentum_score DOUBLE,price_contract_version VARCHAR)')
        db.execute('CREATE TABLE investmitra.screener_signals(isin VARCHAR,screen_name VARCHAR,signal_date DATE)')
        db.execute('CREATE TABLE investmitra.value_quality(isin VARCHAR,piotroski_score INTEGER,graham_criteria_met INTEGER)')
        for isin,symbol,version,priced,sector in [
            ('1','GOOD',VERSION,True,'Energy'),('2','DUP',VERSION,True,'Energy'),
            ('3',' dup ',VERSION,True,'Energy'),('4','LEGACY','daily-venue-v1',True,'Energy'),
            ('5','UNPRICED',VERSION,False,'Energy'),('6','UNKNOWN',VERSION,True,'Unknown')]:
            db.execute("INSERT INTO investmitra.company_master VALUES (?,?,?,'SMALL')",[isin,symbol,sector])
            db.execute("INSERT INTO investmitra.daily_scores VALUES (?,?,'Fixture',?,70,'BUY',70,?)",[isin,DAY,sector,version])
            db.execute("INSERT INTO investmitra.equity_prices VALUES (?,?,'NSE',100,100000)",[isin,DAY-timedelta(days=1)])
            if priced:db.execute("INSERT INTO investmitra.equity_prices VALUES (?,?,'NSE',100,100000)",[isin,DAY])
        tree=ast.parse((ROOT/'scripts/intraday_signals.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='get_signal_catalog')
        sql=max((n.args[0].value for n in ast.walk(fn) if isinstance(n,ast.Call)
                 and isinstance(n.func,ast.Attribute) and n.func.attr=='execute'),key=len)
        sql=sql.replace('%s','?').replace("(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Kolkata')::date","DATE '2026-10-06'")
        self.assertEqual([r[2] for r in db.execute(sql,[VERSION]).fetchall()],['GOOD'])

    def test_price_loader_uses_same_ambiguity_rule_as_features(self):
        from test_pipeline_dates import load_function
        fn=load_function('load_prices_to_neon.py','get_symbol_to_isin',
                         _SYMBOL_TO_ISIN={},identity_map=identity_map,
                         load_identity_rows=lambda:[('A',A),('DUP',A),(' dup ',B)])
        self.assertEqual(fn(),{'A':A})

    def test_price_loader_enriches_only_missing_identities_in_mixed_file(self):
        from test_pipeline_dates import load_function
        conn=MagicMock();writer=Mock()
        fn=load_function('load_prices_to_neon.py','write_to_neon',pd=pd,
            get_symbol_to_isin=lambda:{'A':A},psycopg2=Mock(connect=Mock(return_value=conn)),
            NEON_URL='fixture',execute_values=writer)
        df=pd.DataFrame([dict(isin=isin,nse_symbol=symbol,open=100,high=101,low=99,
                            close=100,volume=1000) for isin,symbol in [(None,' a '),(B,'A'),(None,'DUP')]])
        self.assertEqual(fn(df,DAY,'nse_bhavcopy'),2)
        self.assertEqual({r[0] for r in writer.call_args.args[2]},{A,B})
        conn.commit.assert_called_once()

    def test_price_loader_does_not_infer_bse_identities(self):
        from test_pipeline_dates import load_function
        mapper=Mock(side_effect=AssertionError('must not map BSE'))
        fn=load_function('load_prices_to_neon.py','write_to_neon',pd=pd,get_symbol_to_isin=mapper)
        df=pd.DataFrame([dict(isin=None,nse_symbol='A',open=100,high=101,low=99,close=100,volume=1000)])
        self.assertEqual(fn(df,DAY,'bse_eod'),0)
        mapper.assert_not_called()

    def test_price_loader_enriches_all_null_numeric_isin_from_parquet(self):
        # All-null Parquet columns can arrive as pandas nullable integers.
        # Exercise the real DuckDB -> pandas boundary, including an unmapped row.
        from test_pipeline_dates import load_function
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'nse.parquet'
            db=duckdb.connect()
            try:
                db.execute("COPY (SELECT NULL::INTEGER AS isin, symbol AS nse_symbol, "
                    "100 AS open,101 AS high,99 AS low,100 AS close,1000 AS volume "
                    "FROM (VALUES (' a '),('UNMAPPED')) t(symbol)) TO ? (FORMAT PARQUET)",[str(path)])
                frame=db.execute('SELECT * FROM read_parquet(?)',[str(path)]).df()
            finally: db.close()
        self.assertEqual(str(frame['isin'].dtype),'Int32')
        original=frame.copy(deep=True)
        conn=MagicMock();writer=Mock()
        fn=load_function('load_prices_to_neon.py','write_to_neon',pd=pd,
            get_symbol_to_isin=lambda:{'A':A},psycopg2=Mock(connect=Mock(return_value=conn)),
            NEON_URL='fixture',execute_values=writer)
        self.assertEqual(fn(frame,DAY,'nse_bhavcopy'),1)
        rows=writer.call_args.args[2]
        self.assertEqual(rows[0][0],A)
        self.assertEqual(rows[0][3:7],(100.0,101.0,99.0,100.0))
        self.assertEqual(rows[0][8],1000)
        conn.commit.assert_called_once()
        pd.testing.assert_frame_equal(frame,original)


if __name__=='__main__':unittest.main()
