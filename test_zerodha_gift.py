import requests
from bs4 import BeautifulSoup
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
r = requests.get('https://zerodha.com/market/giftnifty/', headers=headers, timeout=15)
print('Status:', r.status_code)
soup = BeautifulSoup(r.text, 'html.parser')
# Look for price data
for tag in ['span', 'div', 'p']:
    els = soup.find_all(tag, class_=lambda x: x and any(k in str(x).lower() for k in ['price', 'value', 'nifty', 'gift']))
    for el in els[:3]:
        print(f'{tag}: {el.text.strip()[:50]}')
