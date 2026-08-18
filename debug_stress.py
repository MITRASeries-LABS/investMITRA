import psycopg2, os, pandas as pd
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("""
    WITH ranked AS (
        SELECT isin, period_end, revenue_cr, ebitda_cr, pat_cr,
               total_debt_cr, cash_cr, equity_cr,
               ROW_NUMBER() OVER (PARTITION BY isin ORDER BY period_end DESC) AS rn
        FROM investmitra.company_financials
        WHERE period_type = 'Q'
    )
    SELECT isin, period_end, revenue_cr, pat_cr, total_debt_cr, equity_cr, rn
    FROM ranked WHERE rn <= 4 AND isin = 'INE002A01018'
""")
rows = cur.fetchall()
for r in rows: print(r)
conn.close()
