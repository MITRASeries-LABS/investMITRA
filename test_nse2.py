import requests, json
headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json', 'Referer': 'https://www.nseindia.com'}
session = requests.Session()
session.get('https://www.nseindia.com', headers=headers, timeout=10)
urls = [
    'https://www.nseindia.com/api/bulk-deals?from=18-08-2026&to=18-08-2026',
    'https://www.nseindia.com/api/block-deals',
    'https://www.nseindia.com/api/corporate-announcements?index=equities',
]
for url in urls:
    r = session.get(url, headers=headers, timeout=10)
    print(f'{r.status_code} ? {url[:60]}')
    if r.status_code == 200:
        data = r.json()
        if isinstance(data, list):
            print(f'  Records: {len(data)}')
            if data: print(f'  First: {json.dumps(data[0])[:150]}')
        elif isinstance(data, dict):
            print(f'  Keys: {list(data.keys())[:5]}')
