import psycopg2, os, re
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("SELECT isin, company_name, nse_symbol FROM investmitra.company_master WHERE company_name IS NOT NULL LIMIT 10")
companies = cur.fetchall()
cur.execute("SELECT headline FROM investmitra.news_events LIMIT 5")
headlines = [r[0] for r in cur.fetchall()]
conn.close()
for headline in headlines:
    for isin, name, symbol in companies:
        if name and name.lower()[:8] in headline.lower():
            print(f'MATCH: {name} -> {headline[:60]}')
            break
    else:
        print(f'NO MATCH: {headline[:60]}')
