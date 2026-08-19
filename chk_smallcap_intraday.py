import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("""
    SELECT cm.nse_symbol, cm.company_name, cm.market_cap_category,
           ds.investmitra_score, AVG(ep.volume) as avg_vol,
           AVG(ep.close) as avg_price
    FROM investmitra.daily_scores ds
    JOIN investmitra.company_master cm ON ds.isin=cm.isin
    JOIN investmitra.equity_prices ep ON ds.isin=ep.isin
    WHERE ds.score_date=(SELECT MAX(score_date) FROM investmitra.daily_scores)
      AND cm.market_cap_category IN ('SMALL','MICRO')
      AND ds.investmitra_score >= 65
      AND ep.trade_date >= CURRENT_DATE - INTERVAL '30 days'
    GROUP BY cm.nse_symbol, cm.company_name, cm.market_cap_category, ds.investmitra_score
    HAVING AVG(ep.volume) > 100000
       AND AVG(ep.close) BETWEEN 50 AND 2000
    ORDER BY ds.investmitra_score DESC
    LIMIT 20
""")
print(f'{"Symbol":<15} {"Cap":<8} {"Score":>6} {"Avg Vol":>10} {"Price":>8}')
print('-'*55)
for r in cur.fetchall():
    print(f'{str(r[0]):<15} {str(r[2]):<8} {float(r[3]):>6.1f} {int(r[4]):>10,} {float(r[5]):>8.0f}')
conn.close()
