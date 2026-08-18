import requests, json
headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json', 'Referer': 'https://www.nseindia.com'}
session = requests.Session()
session.get('https://www.nseindia.com', headers=headers, timeout=10)
r = session.get('https://www.nseindia.com/api/corporate-announcements?index=equities', headers=headers, timeout=10)
data = r.json()
print(f'Total: {len(data)} announcements')
print()
for item in data[:10]:
    print(f"Symbol: {item.get('symbol','')}")
    print(f"Time:   {item.get('an_dt','')}")
    print(f"Type:   {item.get('desc','') or item.get('subject','')}")
    print(f"File:   {str(item.get('attchmntFile',''))[:60]}")
    print()
