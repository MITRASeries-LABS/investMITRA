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
    SELECT * FROM ranked WHERE rn <= 4
""")
rows = cur.fetchall()
df = pd.DataFrame(rows, columns=['isin','period_end','revenue_cr','ebitda_cr','ebit_cr','pat_cr','total_debt_cr','cash_cr','equity_cr','rn'])
conn.close()

def best_val(g, col):
    vals = g.sort_values('rn')[col].dropna()
    return vals.iloc[0] if len(vals) > 0 else None

latest = df[df['rn']==1].set_index('isin').copy()
for col in ['total_debt_cr','cash_cr','equity_cr']:
    best = df.groupby('isin').apply(lambda g: best_val(g, col))
    latest[col] = latest.index.map(best)
latest = latest.reset_index()

print('With revenue:', latest['revenue_cr'].notna().sum())
print('With debt:', latest['total_debt_cr'].notna().sum())
print('With equity:', latest['equity_cr'].notna().sum())

# Compute debt_equity
latest['debt_equity'] = latest['total_debt_cr'] / latest['equity_cr'].replace(0, float('nan'))
latest['pat_margin'] = latest['pat_cr'] / latest['revenue_cr'].replace(0, float('nan'))
print('With debt_equity:', latest['debt_equity'].notna().sum())
print('With pat_margin:', latest['pat_margin'].notna().sum())
print(latest[latest['debt_equity'].notna()][['isin','debt_equity','pat_margin']].head(5))
