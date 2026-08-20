"""
investMITRA - Fetch F&O eligible stocks from NSE
These are the most liquid stocks - tight spreads, institutional participation
Saves to Neon for use as intraday watchlist filter
"""
import requests, psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')

headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json', 'Referer': 'https://www.nseindia.com'}
session = requests.Session()
session.get('https://www.nseindia.com', headers=headers, timeout=10)
r = session.get('https://www.nseindia.com/api/master-quote', headers=headers, timeout=10)
fo_stocks = r.json()
print(f'F&O stocks: {len(fo_stocks)}')

conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
conn.autocommit = True
cur = conn.cursor()
cur.execute('''CREATE TABLE IF NOT EXISTS investmitra.fo_stocks
    (symbol VARCHAR(20) PRIMARY KEY, updated_at TIMESTAMPTZ DEFAULT NOW())''')
cur.execute('DELETE FROM investmitra.fo_stocks')
for sym in fo_stocks:
    cur.execute('INSERT INTO investmitra.fo_stocks (symbol) VALUES (%s) ON CONFLICT DO NOTHING', (sym,))
print(f'Saved {len(fo_stocks)} F&O stocks to Neon')
conn.close()
