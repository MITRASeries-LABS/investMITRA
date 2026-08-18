import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
conn.autocommit = True
cur = conn.cursor()
cur.execute("ALTER TABLE investmitra.daily_scores ADD COLUMN IF NOT EXISTS company_name VARCHAR(200)")
cur.execute("UPDATE investmitra.daily_scores ds SET company_name = cm.company_name FROM investmitra.company_master cm WHERE ds.isin = cm.isin AND ds.company_name IS NULL")
cur.execute("SELECT COUNT(*) FROM investmitra.daily_scores WHERE company_name IS NOT NULL")
print('With company name:', cur.fetchone()[0])
conn.close()
