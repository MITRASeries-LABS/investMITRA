import requests
from bs4 import BeautifulSoup
r = requests.get('https://www.sebi.gov.in/sebirss.xml', timeout=10)
print('Status:', r.status_code)
if r.status_code == 200:
    soup = BeautifulSoup(r.text, 'lxml')
    items = soup.find_all('item')[:5]
    print('Items:', len(items))
    for item in items:
        title = item.find('title')
        pub   = item.find('pubdate')
        print(f'  {title.text.strip()[:70] if title else "?"}')
        print(f'  {pub.text.strip() if pub else "?"}')
        print()
