import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
conn.autocommit = True
cur = conn.cursor()

cur.execute("DELETE FROM investmitra.news_events WHERE published_at < CURRENT_DATE - INTERVAL '30 days'")
print(f'news_events: deleted {cur.rowcount} rows')

cur.execute("VACUUM ANALYZE")
print('Vacuum done')
conn.close()
