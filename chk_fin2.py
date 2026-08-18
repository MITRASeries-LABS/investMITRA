import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("SELECT COUNT(*), COUNT(DISTINCT isin) FROM investmitra.company_financials")
r = cur.fetchone()
print(f'Records: {r[0]}, ISINs: {r[1]}')
conn.close()
