import requests
from bs4 import BeautifulSoup
urls = [
    'https://www.sebi.gov.in/sebi_data/rss/rss_sebi.xml',
    'https://www.sebi.gov.in/rss/rss_sebi_news.xml',
    'https://www.sebi.gov.in/rss/rss_sebi_orders.xml',
]
for url in urls:
    r = requests.get(url, timeout=10)
    print(f'{r.status_code} ? {url}')
    if r.status_code == 200:
        soup = BeautifulSoup(r.text, 'lxml')
        items = soup.find_all('item')[:3]
        print(f'  Items: {len(items)}')
        for item in items:
            title = item.find('title')
            print(f'  ? {title.text[:60] if title else "no title"}')
    print()
