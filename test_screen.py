import requests
from bs4 import BeautifulSoup
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
r = requests.get('https://www.screener.in/screens/1/', headers=headers, timeout=20)
print('Status:', r.status_code)
soup = BeautifulSoup(r.text, 'html.parser')
table = soup.find('table')
print('Table found:', table is not None)
if table:
    rows = table.find_all('tr')
    print('Rows:', len(rows))
    if len(rows) > 1:
        cells = rows[1].find_all(['td','th'])
        print('First row cells:', [c.get_text(strip=True)[:20] for c in cells[:5]])
