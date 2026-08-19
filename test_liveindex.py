import requests
from bs4 import BeautifulSoup
headers = {'User-Agent': 'Mozilla/5.0'}
r = requests.get('https://liveindex.org/', headers=headers, timeout=15)
print('Status:', r.status_code)
soup = BeautifulSoup(r.text, 'html.parser')
# Look for table rows with index data
rows = soup.find_all('tr')[:10]
for row in rows:
    cells = row.find_all(['td','th'])
    if cells:
        text = [c.get_text(strip=True)[:20] for c in cells[:4]]
        if any(t for t in text):
            print(text)
