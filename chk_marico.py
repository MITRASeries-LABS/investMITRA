import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("""
    SELECT ep.trade_date, ep.open, ep.high, ep.low, ep.close, ep.volume
    FROM investmitra.equity_prices ep
    JOIN investmitra.company_master cm ON ep.isin = cm.isin
    WHERE cm.nse_symbol = 'MARICO'
    ORDER BY ep.trade_date DESC
    LIMIT 5
""")
print(f'{"Date":<12} {"Open":>8} {"High":>8} {"Low":>8} {"Close":>8} {"Volume":>12}')
print('-'*60)
for r in cur.fetchall():
    print(f'{str(r[0]):<12} {float(r[1]):>8.2f} {float(r[2]):>8.2f} {float(r[3]):>8.2f} {float(r[4]):>8.2f} {int(r[5]):>12,}')
conn.close()
