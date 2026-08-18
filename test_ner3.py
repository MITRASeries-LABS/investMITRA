import psycopg2, os, re
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("SELECT isin, company_name, nse_symbol FROM investmitra.company_master WHERE company_name IS NOT NULL")
companies = [(r[0], r[1], r[2]) for r in cur.fetchall()]
cur.execute("SELECT event_id, headline FROM investmitra.news_events LIMIT 20")
headlines = cur.fetchall()
conn.close()

# Common words to exclude from matching
STOP = {'global','india','market','share','shares','price','stock','limited','ltd',
        'action','advance','force','oil','central','blue','express','urban'}

matched = 0
for event_id, headline in headlines:
    hl_lower = headline.lower()
    found = []
    for isin, name, symbol in companies:
        # Match NSE symbol (exact word, min 4 chars)
        if symbol and len(symbol) >= 4:
            pattern = r'\b' + re.escape(symbol.upper()) + r'\b'
            if re.search(pattern, headline):
                found.append((isin, symbol, 'sym'))
                continue
        # Match company name - first 2 meaningful words
        if name:
            words = [w for w in name.split()[:3] if len(w) > 4 and w.lower() not in STOP]
            if words and all(w.lower() in hl_lower for w in words[:2]):
                found.append((isin, name[:25], 'name'))
    if found:
        print(f'MATCH {found[0]}: {headline[:65]}')
        matched += 1
    else:
        print(f'NO MATCH: {headline[:65]}')
print(f'Matched {matched}/20')
