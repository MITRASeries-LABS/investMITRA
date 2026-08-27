import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
conn.autocommit = True
cur = conn.cursor()

# Drop old year tables - only need 2025-2026 for ATR calculations
tables_to_drop = [
    'equity_prices_2014', 'equity_prices_2015', 'equity_prices_2016',
    'equity_prices_2017', 'equity_prices_2018', 'equity_prices_2019',
    'equity_prices_2020', 'equity_prices_2021', 'equity_prices_2022',
    'equity_prices_2023', 'equity_prices_2024',
]

for t in tables_to_drop:
    try:
        cur.execute(f'DROP TABLE IF EXISTS investmitra.{t}')
        print(f'Dropped: {t}')
    except Exception as e:
        print(f'Error {t}: {e}')

# Also clean news_events older than 30 days
try:
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='investmitra' AND table_name='news_events' LIMIT 5")
    cols = [r[0] for r in cur.fetchall()]
    print(f'news_events columns: {cols}')
except: pass

cur.execute("VACUUM ANALYZE")
print('Vacuum done')
conn.close()
print('Done! Check Neon storage now.')
