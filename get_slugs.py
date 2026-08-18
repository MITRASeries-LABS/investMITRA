import requests
from bs4 import BeautifulSoup
headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.screener.in'}
# Test a few screen IDs to get their slugs
ids = ['1','86','59','343087','440753','579']
for sid in ids:
    r = requests.get(f'https://www.screener.in/screens/{sid}/', headers=headers, timeout=10, allow_redirects=True)
    print(f'{sid}: {r.status_code} -> {r.url}')
