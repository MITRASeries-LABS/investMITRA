import requests
headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json', 'Referer': 'https://www.nseindia.com'}
session = requests.Session()
session.get('https://www.nseindia.com', headers=headers, timeout=10)
r = session.get('https://www.nseindia.com/api/allIndices', headers=headers, timeout=10)
data = r.json().get('data', [])
for idx in data:
    if 'VIX' in idx.get('index','').upper():
        print(f"{idx['index']}: {idx['last']} ({idx.get('percentChange',0):+.2f}%)")
