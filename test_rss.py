import requests
from bs4 import BeautifulSoup
r = requests.get('https://economictimes.indiatimes.com/markets/rss.cms', timeout=10)
print('Status:', r.status_code)
soup = BeautifulSoup(r.text, 'xml')
items = soup.find_all('item')[:5]
for item in items:
    print(item.find('title').text[:60])
    print(item.find('pubDate').text)
    print()
