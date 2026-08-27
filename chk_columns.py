import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
tables = ['sebi_updates', 'corporate_events', 'daily_scores', 'screener_signals']
for t in tables:
    cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_schema='investmitra' AND table_name='{t}' AND column_name LIKE '%date%' OR column_name LIKE '%at%' AND table_schema='investmitra' AND table_name='{t}'")
    cols = [r[0] for r in cur.fetchall()]
    print(f'{t}: {cols}')
conn.close()
