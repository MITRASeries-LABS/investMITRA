import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM investmitra.ownership_data")
print('Rows:', cur.fetchone()[0])
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='investmitra' AND table_name='ownership_data' ORDER BY ordinal_position")
print('Columns:', [r[0] for r in cur.fetchall()])
cur.execute("SELECT * FROM investmitra.ownership_data LIMIT 2")
for r in cur.fetchall(): print(r)
conn.close()
