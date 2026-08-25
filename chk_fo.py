import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM investmitra.fo_stocks")
print(f'Total F&O stocks: {cur.fetchone()[0]}')
cur.execute("SELECT symbol FROM investmitra.fo_stocks WHERE symbol IN ('HINDCOPPER','MCX','HAL','BEL','ATLANTAELE','EMMVEE')")
print(f'Found: {[r[0] for r in cur.fetchall()]}')
conn.close()
