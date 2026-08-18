import requests
from bs4 import BeautifulSoup
r = requests.get('https://economictimes.indiatimes.com/markets/rss.cms', timeout=10)
soup = BeautifulSoup(r.text, 'lxml')
items = soup.find_all('item')[:5]
print('Items found:', len(items))
for item in items:
    title = item.find('title')
    pub   = item.find('pubdate')
    print(title.text[:70] if title else 'no title')
    print(pub.text if pub else '')
    print()
