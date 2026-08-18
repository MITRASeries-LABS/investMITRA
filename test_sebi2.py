import requests
from bs4 import BeautifulSoup
headers = {'User-Agent': 'Mozilla/5.0'}
urls = [
    'https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=1&ssid=2&smid=0',
    'https://www.sebi.gov.in/pressrelease/index.html',
    'https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=6&ssid=18&smid=0',
]
for url in urls:
    r = requests.get(url, headers=headers, timeout=10)
    print(f'{r.status_code} ? {url[:60]}')
    if r.status_code == 200:
        soup = BeautifulSoup(r.text, 'html.parser')
        print(f'  Page size: {len(r.text)} chars')
        links = soup.find_all('a', href=True)[:5]
        for l in links[:3]:
            print(f'  ? {l.text.strip()[:50]}')
    print()
