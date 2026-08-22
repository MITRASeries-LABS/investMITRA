import requests, psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json', 'Referer': 'https://www.nseindia.com'}
session = requests.Session()
session.get('https://www.nseindia.com', headers=headers, timeout=10)
r = session.get('https://www.nseindia.com/api/master-quote', headers=headers, timeout=10)
print('Status:', r.status_code)
if r.status_code == 200:
    data = r.json()
    print('Type:', type(data))
    print('Sample:', str(data)[:200])
