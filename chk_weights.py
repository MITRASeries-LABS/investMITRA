import psycopg2, os, json
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("SELECT effective_date, weights, notes FROM investmitra.signal_weights ORDER BY effective_date DESC LIMIT 2")
for r in cur.fetchall():
    print(f'Date: {r[0]}')
    w = r[1] if isinstance(r[1], dict) else json.loads(r[1])
    for k,v in w.items():
        print(f'  {k}: {v}')
    print(f'Notes: {str(r[2])[:100]}')
    print()
conn.close()
