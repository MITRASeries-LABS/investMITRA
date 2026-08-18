import requests
from bs4 import BeautifulSoup
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
r = requests.get('https://trendlyne.com/equity/calendar/all/all/', headers=headers, timeout=15)
print('Status:', r.status_code)
soup = BeautifulSoup(r.text, 'html.parser')
tables = soup.find_all('table')
print('Tables found:', len(tables))
if tables:
    rows = tables[0].find_all('tr')[:5]
    for row in rows:
        cells = row.find_all(['td','th'])
        print([c.get_text(strip=True)[:30] for c in cells[:5]])
