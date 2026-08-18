import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM investmitra.screener_signals")
print('Rows:', cur.fetchone()[0])
cur.execute("SELECT signal_date, COUNT(*) FROM investmitra.screener_signals GROUP BY signal_date ORDER BY signal_date DESC LIMIT 5")
for r in cur.fetchall(): print(r)
conn.close()
