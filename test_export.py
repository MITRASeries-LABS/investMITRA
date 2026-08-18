import requests
headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.screener.in', 'X-Requested-With': 'XMLHttpRequest'}
# Try JSON export endpoint
urls = [
    'https://www.screener.in/screens/1/the-bull-cartel/?export=json',
    'https://www.screener.in/screens/1/the-bull-cartel/?format=json',
    'https://www.screener.in/api/screens/1/the-bull-cartel/',
]
for url in urls:
    r = requests.get(url, headers=headers, timeout=15)
    print(f'{r.status_code} ? {url[:60]}')
    if r.status_code == 200:
        print('  Content:', r.text[:200])
