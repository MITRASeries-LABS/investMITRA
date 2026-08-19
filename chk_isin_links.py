import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM investmitra.news_events WHERE entities_isin IS NOT NULL")
print('Articles with ISIN:', cur.fetchone()[0])
cur.execute("SELECT LEFT(headline,50), entities_isin, sentiment_score FROM investmitra.news_events WHERE entities_isin IS NOT NULL ORDER BY ingested_at DESC LIMIT 5")
for r in cur.fetchall():
    print(f'  ISIN:{r[1]} Score:{float(r[2] or 0):+.2f} {r[0]}')
conn.close()
