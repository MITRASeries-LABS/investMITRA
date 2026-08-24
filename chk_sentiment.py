import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("SELECT MAX(fetch_date), COUNT(*) FROM investmitra.global_indices")
r = cur.fetchone()
print(f'Global sentiment: last={r[0]} count={r[1]}')
cur.execute("SELECT MAX(fetch_date), COUNT(*) FROM investmitra.market_indices")
r = cur.fetchone()
print(f'Market indices:   last={r[0]} count={r[1]}')
cur.execute("SELECT index_name, close_value, change_pct FROM investmitra.global_indices WHERE fetch_date=(SELECT MAX(fetch_date) FROM investmitra.global_indices) AND index_name IN ('SGX NIFTY FUTURE','S&P 500 VIX','DOW 30') ")
for r in cur.fetchall():
    print(f'  {r[0]}: {r[1]} ({r[2]:+.2f}%)')
conn.close()
