import psycopg2, os
from dotenv import load_dotenv
import sys
sys.path.insert(0, 'C:/MITRAseries/investMITRA')
load_dotenv('C:/MITRAseries/investMITRA/.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("SELECT isin, company_name, investmitra_score, signal, momentum_score, financial_health_score, management_quality_score, ret_252d_pct FROM investmitra.daily_scores WHERE score_date='2026-08-15' AND isin='INE002A01018'")
r = cur.fetchone()
if r: print(f'Reliance: Score={r[2]}, Signal={r[3]}, Momentum={r[4]}, FinHealth={r[5]}, Mgmt={r[6]}, 1Y_Return={r[7]}')
else: print('Not found')
conn.close()
