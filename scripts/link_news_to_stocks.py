import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')

conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()

cur.execute("SELECT isin, company_name, nse_symbol FROM investmitra.company_master WHERE company_name IS NOT NULL")
companies = [(r[0], r[1].upper(), r[2]) for r in cur.fetchall()]

cur.execute("SELECT event_id, headline FROM investmitra.news_events WHERE entities_isin IS NULL AND sentiment_score IS NOT NULL LIMIT 5000")
articles = cur.fetchall()
print(f'Linking {len(articles)} articles...')

linked = 0
for event_id, headline in articles:
    if not headline: continue
    headline_upper = headline.upper()
    for isin, company_name, symbol in companies:
        short = company_name[:20]
        matched = False
        if len(short) >= 6 and short in headline_upper:
            matched = True
            conf = 0.8
        elif symbol and len(symbol) >= 3 and f' {symbol} ' in f' {headline_upper} ':
            matched = True
            conf = 0.6
        if matched:
            cur.execute("UPDATE investmitra.news_events SET entities_isin=%s::text[], entity_confidence=%s::float8[] WHERE event_id=%s", ([isin], [conf], event_id))
            linked += 1
            break

conn.commit()
print(f'Linked {linked}/{len(articles)} articles')
conn.close()
