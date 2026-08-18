import psycopg2, os, pandas as pd
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("""
    WITH ranked AS (
        SELECT isin, period_end, revenue_cr, ebitda_cr, ebit_cr, pat_cr,
               total_debt_cr, cash_cr, equity_cr,
               ROW_NUMBER() OVER (PARTITION BY isin ORDER BY period_end DESC) AS rn
        FROM investmitra.company_financials WHERE period_type='Q'
    )
    SELECT isin, period_end, revenue_cr, pat_cr, total_debt_cr, equity_cr, rn
    FROM ranked WHERE rn <= 4
""")
rows = cur.fetchall()
df = pd.DataFrame(rows, columns=['isin','period_end','revenue_cr','pat_cr','total_debt_cr','equity_cr','rn'])
conn.close()

print('Total rows:', len(df))
print('With debt:', df['total_debt_cr'].notna().sum())
print('With equity:', df['equity_cr'].notna().sum())

# Test best_val logic
def best_val(g, col):
    vals = g.sort_values('rn')[col].dropna()
    return vals.iloc[0] if len(vals) > 0 else None

latest = df[df['rn']==1].set_index('isin').copy()
best_debt = df.groupby('isin').apply(lambda g: best_val(g, 'total_debt_cr'))
latest['total_debt_cr'] = latest.index.map(best_debt)
print('After fill - with debt:', latest['total_debt_cr'].notna().sum(), 'of', len(latest))
print('Sample:', latest[latest['total_debt_cr'].notna()].head(3)[['total_debt_cr','equity_cr']])
