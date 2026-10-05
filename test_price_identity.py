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
from market_data_contract import VERSION, canonical_price_ctes
from price_identity import identity_map, prepare_price_inputs, load_identity_rows
from signal_input_readiness import audit, summarise, require_ready

ROOT=Path(__file__).parent
DAY=date(2026,10,5)
A='INE000000001'
B='INE000000019'


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
    def test_reported_344_unclassified_rows_are_not_ready(self):
        result=summarise([(str(i),VERSION,'Unknown',None,True,True,60,'Unknown') for i in range(344)],DAY)
        self.assertEqual(result['scored_nse_symbols'],344)
        self.assertEqual(result['eligible_classified_priced_nse_symbols'],0)
        with self.assertRaisesRegex(RuntimeError,'NOT READY'):require_ready(result)

    def test_missing_classified_score_cannot_disappear_from_denominator(self):
        rows=[('A',VERSION,'Energy','SMALL',True,True,70,'Energy'),
              ('B',None,None,'LARGE',True,False,None,'Financial Services')]
        result=summarise(rows,DAY)
        self.assertEqual(result['expected_classified_priced_nse_symbols'],2)
        self.assertEqual(result['missing_classified_scores'],['B']);self.assertFalse(result['ready'])

    def test_complete_classified_universe_with_unknown_exclusions_is_ready(self):
        result=summarise([('A',VERSION,'Energy','SMALL',True,True,70,'Energy'),
                          ('B',VERSION,'Unknown',None,True,True,50,'Unknown')],DAY)
        require_ready(result);self.assertEqual(result['eligible_classified_priced_nse_symbols'],1)
        self.assertEqual(result['missing_sector'],['B'])

    def test_wrong_contract_null_score_or_missing_score_sector_blocks(self):
        for version,sector,score in [('daily-venue-v1','Energy',70),(VERSION,'Energy',float('nan')),
                                    (VERSION,'Unknown',70),(VERSION,'Energy',None)]:
            result=summarise([('A',version,sector,'SMALL',True,True,score,'Energy')],DAY)
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


if __name__=='__main__':unittest.main()
