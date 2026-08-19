import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("""
    SELECT cm.nse_symbol, cm.company_name, cm.market_cap_category,
           ds.investmitra_score, ds.signal,
           ds.momentum_score, ds.financial_health_score,
           ds.management_quality_score,
           COALESCE(ss.screen_count,0) as screens,
           COALESCE(vq.piotroski_score,0) as piotroski
    FROM investmitra.daily_scores ds
    JOIN investmitra.company_master cm ON ds.isin=cm.isin
    LEFT JOIN (SELECT isin, COUNT(DISTINCT screen_name) as screen_count
               FROM investmitra.screener_signals
               WHERE signal_date=(SELECT MAX(signal_date) FROM investmitra.screener_signals)
               GROUP BY isin) ss ON ds.isin=ss.isin
    LEFT JOIN investmitra.value_quality vq ON ds.isin=vq.isin
    WHERE ds.score_date=(SELECT MAX(score_date) FROM investmitra.daily_scores)
      AND cm.nse_symbol IN ('ASTRAL','AIAENG','APARINDS','AEGISLOG','ATUL','ACE','ABSLAMC')
    ORDER BY ds.investmitra_score DESC
""")
print(f'{"Symbol":<12} {"Cap":<7} {"Score":>6} {"Mom":>6} {"Fin":>6} {"Mgmt":>6} {"Scr":>4} {"F":>3} {"Signal"}')
print('-'*75)
for r in cur.fetchall():
    print(f'{r[0]:<12} {r[2]:<7} {float(r[3]):>6.1f} {float(r[5] or 0):>6.1f} {float(r[6] or 0):>6.1f} {float(r[7] or 0):>6.1f} {int(r[8]):>4} {int(r[9]):>3} {r[4]}')
conn.close()
