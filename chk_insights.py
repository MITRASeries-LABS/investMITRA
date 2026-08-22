import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("SELECT symbol, outcome, analysis, issues_found, suggestions, confidence FROM investmitra.trade_insights ORDER BY created_at DESC LIMIT 5")
rows = cur.fetchall()
print(f'Total insights: {len(rows)}')
for r in rows:
    print(f'\n{r[0]} ({r[1]}):')
    print(f'  Analysis: {str(r[2])[:100]}')
    print(f'  Issues: {str(r[3])[:100]}')
    print(f'  Suggestions: {str(r[4])[:100]}')
    print(f'  Confidence: {r[5]}')
conn.close()
