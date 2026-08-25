import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("SELECT symbol FROM investmitra.fo_stocks WHERE symbol LIKE '%COPPER%' OR symbol LIKE '%HIND%'")
print('HIND/COPPER symbols in F&O:', cur.fetchall())
cur.execute("SELECT symbol FROM investmitra.fo_stocks ORDER BY symbol LIMIT 20")
print('First 20:', [r[0] for r in cur.fetchall()])
conn.close()
