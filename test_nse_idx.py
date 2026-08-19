import requests, json
headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json', 'Referer': 'https://www.nseindia.com'}
session = requests.Session()
session.get('https://www.nseindia.com', headers=headers, timeout=10)
r = session.get('https://www.nseindia.com/api/allIndices', headers=headers, timeout=10)
print('Status:', r.status_code)
if r.status_code == 200:
    data = r.json()
    for idx in data.get('data', [])[:5]:
        print(f"{idx.get('index')}: {idx.get('last')} ({idx.get('percentChange'):+.2f}%)")
