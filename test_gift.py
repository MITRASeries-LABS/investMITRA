import requests
from bs4 import BeautifulSoup
r = requests.get('https://giftcitynifty.com/', headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
print('Status:', r.status_code)
soup = BeautifulSoup(r.text, 'html.parser')
print(r.text[:500])
