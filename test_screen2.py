import requests
from bs4 import BeautifulSoup
headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.screener.in'}
# Try different URL formats
urls = [
    'https://www.screener.in/screens/1/',
    'https://www.screener.in/screens/1/the-bull-cartel/',
    'https://www.screener.in/api/screens/1/',
]
for url in urls:
    r = requests.get(url, headers=headers, timeout=15)
    print(f'{r.status_code} ? {url}')
    if r.status_code == 200:
        print('  Content:', r.text[:100])
