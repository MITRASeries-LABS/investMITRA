import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
isins = ['INE034S01021','INE176J01011','INE010A01011','INE417N01011','INE064H01021','INE0DNW01011']
cur.execute("SELECT isin, company_name, sector, market_cap_category FROM investmitra.company_master WHERE isin = ANY(%s)", (isins,))
for r in cur.fetchall(): print(r)
conn.close()
