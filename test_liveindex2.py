import requests
from bs4 import BeautifulSoup
headers = {'User-Agent': 'Mozilla/5.0'}
r = requests.get('https://liveindex.org/', headers=headers, timeout=15)
soup = BeautifulSoup(r.text, 'html.parser')
rows = soup.find_all('tr')
for row in rows:
    cells = row.find_all(['td','th'])
    text  = [c.get_text(strip=True) for c in cells]
    if any('GIFT' in t or 'NIFTY' in t or 'INDIA' in t or 'SGX' in t for t in text):
        print(text[:5])
