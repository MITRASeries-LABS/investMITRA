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

# Build search terms - use symbol and first meaningful word of company name
matched = 0
for event_id, headline in headlines:
    hl = headline.lower()
    for isin, name, symbol in companies:
        # Match on NSE symbol
        if symbol and len(symbol) >= 3 and symbol.lower() in hl.split():
            print(f'SYM MATCH [{symbol}]: {headline[:70]}')
            matched += 1
            break
        # Match on first word of company name (>4 chars)
        if name:
            first_word = name.split()[0].lower()
            if len(first_word) > 4 and first_word in hl:
                print(f'NAME MATCH [{name[:20]}]: {headline[:70]}')
                matched += 1
                break
print(f'Matched {matched}/20 headlines')
