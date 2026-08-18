import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
isins = ['INE138I08091','INE682M01020','INE229G01022','INE031A07915','INE138I08083']
cur.execute("SELECT isin, company_name, sector, market_cap_category FROM investmitra.company_master WHERE isin = ANY(%s)", (isins,))
for r in cur.fetchall(): print(r)
conn.close()
