import requests, json
headers = {
    'User-Agent': 'Mozilla/5.0',
    'Accept': 'application/json',
    'Referer': 'https://www.nseindia.com'
}
session = requests.Session()
session.get('https://www.nseindia.com', headers=headers, timeout=10)
r = session.get('https://www.nseindia.com/api/event-calendar', headers=headers, timeout=10)
print('Status:', r.status_code)
if r.status_code == 200:
    data = r.json()
    print('Type:', type(data))
    if isinstance(data, list):
        print('Records:', len(data))
        print('First:', json.dumps(data[0], indent=2) if data else 'empty')
    else:
        print(str(data)[:300])
