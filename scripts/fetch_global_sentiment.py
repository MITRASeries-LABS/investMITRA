import requests, psycopg2, os
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
load_dotenv('.env.prod')
IST = timezone(timedelta(hours=5, minutes=30))
headers = {'User-Agent': 'Mozilla/5.0'}
r = requests.get('https://liveindex.org/', headers=headers, timeout=15)
soup = BeautifulSoup(r.text, 'html.parser')
rows = soup.find_all('tr')
indices = []
for row in rows:
    cells = row.find_all(['td','th'])
    if len(cells) >= 3:
        name   = cells[0].get_text(strip=True)
        last   = cells[2].get_text(strip=True).replace(',','')
        change = cells[3].get_text(strip=True).replace(',','') if len(cells) > 3 else '0'
        if name and last and any(c.isdigit() for c in last):
            try:
                indices.append((name, float(last), change))
            except: pass
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
conn.autocommit = True
cur = conn.cursor()
cur.execute('''CREATE TABLE IF NOT EXISTS investmitra.global_indices
    (id SERIAL PRIMARY KEY, index_name VARCHAR(100), last_price DECIMAL(15,2),
     change_str VARCHAR(30), fetch_date DATE DEFAULT CURRENT_DATE,
     fetched_at TIMESTAMPTZ DEFAULT NOW())''')
cur.execute('DELETE FROM investmitra.global_indices WHERE fetch_date = CURRENT_DATE')
for name, last, chg in indices:
    cur.execute('INSERT INTO investmitra.global_indices (index_name, last_price, change_str) VALUES (%s,%s,%s)', (name, last, chg))
conn.close()
print(f'Saved {len(indices)} global indices')
# Print key ones
key = ['DOW','NASDAQ','S&P 500','SGX','NIFTY 50','NIKKEI','HANG SENG','DAX']
for name, last, chg in indices:
    if any(k in name.upper() for k in key):
        print(f'  {name:<25} {last:>12,.2f}  {chg}')
