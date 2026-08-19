import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("""
    SELECT cm.nse_symbol, cm.market_cap_category,
           ROUND(AVG(ep.volume)::numeric,0) as avg_vol,
           ROUND(AVG(ep.close)::numeric,2) as avg_price,
           ROUND(AVG(ep.volume)*AVG(ep.close)::numeric,0) as avg_traded
    FROM investmitra.equity_prices ep
    JOIN investmitra.company_master cm ON ep.isin=cm.isin
    WHERE cm.nse_symbol IN ('ASTRAL','AIAENG','ABSLAMC','APARINDS','AEGISLOG','ATUL','ACE')
      AND ep.trade_date >= CURRENT_DATE - INTERVAL '30 days'
    GROUP BY cm.nse_symbol, cm.market_cap_category
    ORDER BY avg_traded DESC
""")
print(f'{"Symbol":<12} {"Cap":<7} {"Avg Vol":>12} {"Price":>8} {"Traded Value":>15} {"Pass?"}')
print('-'*65)
for r in cur.fetchall():
    avg_vol    = int(r[2])
    avg_traded = float(r[4])
    cap        = r[1]
    if cap in ('MID','LARGE'):
        passes = avg_vol >= 200000
    else:
        passes = avg_traded >= 50_000_000 and avg_vol >= 100000
    print(f'{r[0]:<12} {cap:<7} {avg_vol:>12,} {float(r[3]):>8.2f} {avg_traded:>15,.0f}  {"?" if passes else "?"}')
conn.close()
