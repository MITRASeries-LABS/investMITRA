import requests, psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json', 'Referer': 'https://www.nseindia.com'}
session = requests.Session()
session.get('https://www.nseindia.com', headers=headers, timeout=10)
r = session.get('https://www.nseindia.com/api/allIndices', headers=headers, timeout=10)
data = r.json().get('data', [])
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
conn.autocommit = True
cur = conn.cursor()
cur.execute('''CREATE TABLE IF NOT EXISTS investmitra.market_indices
    (id SERIAL PRIMARY KEY, index_name VARCHAR(100), last_price DECIMAL(12,2),
     change_pct DECIMAL(8,4), fetch_date DATE DEFAULT CURRENT_DATE,
     fetched_at TIMESTAMPTZ DEFAULT NOW())''')
cur.execute('DELETE FROM investmitra.market_indices WHERE fetch_date = CURRENT_DATE')
for idx in data:
    cur.execute('INSERT INTO investmitra.market_indices (index_name, last_price, change_pct) VALUES (%s,%s,%s)',
        (idx.get('index'), idx.get('last'), idx.get('percentChange')))
print(f'Saved {len(data)} indices')
conn.close()
