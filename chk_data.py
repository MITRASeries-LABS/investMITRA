import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM investmitra.equity_prices WHERE trade_date >= CURRENT_DATE - INTERVAL '30 days'")
print('Price rows:', cur.fetchone()[0])
cur.execute('SELECT MAX(score_date) FROM investmitra.daily_scores')
print('Latest scores:', cur.fetchone()[0])
cur.execute('SELECT COUNT(DISTINCT isin) FROM investmitra.equity_prices WHERE trade_date >= CURRENT_DATE - INTERVAL 30 * interval 1 day')
conn.close()
