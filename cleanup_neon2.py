import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
conn.autocommit = True
cur = conn.cursor()

# Fix column names
cur.execute("DELETE FROM investmitra.nse_announcements WHERE fetched_at < CURRENT_DATE - INTERVAL '30 days'")
print(f'nse_announcements: deleted {cur.rowcount} rows')

cur.execute("DELETE FROM investmitra.sebi_updates WHERE created_at < CURRENT_DATE - INTERVAL '30 days'")
print(f'sebi_updates: deleted {cur.rowcount} rows')

cur.execute("DELETE FROM investmitra.corporate_events WHERE event_date < CURRENT_DATE - INTERVAL '30 days'")
print(f'corporate_events: deleted {cur.rowcount} rows')

cur.execute("DELETE FROM investmitra.daily_scores WHERE score_date < CURRENT_DATE - INTERVAL '30 days'")
print(f'daily_scores: deleted {cur.rowcount} rows')

cur.execute("VACUUM ANALYZE")
print('Vacuum done')
conn.close()
print('Cleanup complete!')
