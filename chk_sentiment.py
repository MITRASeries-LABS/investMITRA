import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM investmitra.news_events WHERE sentiment_score IS NOT NULL")
print('Scored:', cur.fetchone()[0])
cur.execute("SELECT headline[:60], sentiment_score, sentiment_label FROM investmitra.news_events WHERE sentiment_score IS NOT NULL ORDER BY ingested_at DESC LIMIT 5")
for r in cur.fetchall():
    print(f'  {r[1]:+.2f} {r[2]:<10} {r[0]}')
conn.close()
