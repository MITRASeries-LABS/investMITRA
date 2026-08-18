import requests, json
headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json', 'Referer': 'https://www.nseindia.com'}
session = requests.Session()
session.get('https://www.nseindia.com', headers=headers, timeout=10)
r = session.get('https://www.nseindia.com/api/bulk-deals', headers=headers, timeout=10)
print('Status:', r.status_code)
if r.status_code == 200:
    data = r.json()
    print('Type:', type(data))
    print('Keys:', data.keys() if isinstance(data, dict) else 'list')
    if isinstance(data, list) and data:
        print('Records:', len(data))
        print('First:', json.dumps(data[0], indent=2))
    elif isinstance(data, dict):
        for k, v in data.items():
            print(f'{k}: {str(v)[:100]}')
