import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("SELECT MAX(fetch_date), COUNT(*) FROM investmitra.market_indices")
r = cur.fetchone()
print(f'Market indices last fetch: {r[0]} ({r[1]} records)')
cur.execute("SELECT MAX(fetch_date), COUNT(*) FROM investmitra.global_indices")
r = cur.fetchone()
print(f'Global indices last fetch: {r[0]} ({r[1]} records)')
conn.close()
