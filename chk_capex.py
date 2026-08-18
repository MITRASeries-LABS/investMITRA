import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("""
    SELECT cm.company_name, cm.sector, cm.market_cap_category,
           sd.rev_growth_3yr_pct, sd.fa_doubled, sd.cwip_surge_50pct,
           ds.investmitra_score, ds.signal,
           sd.revenue_cr[1] as rev_cr
    FROM investmitra.screener_data sd
    JOIN investmitra.company_master cm ON sd.isin = cm.isin
    JOIN investmitra.daily_scores ds ON sd.isin = ds.isin
        AND ds.score_date = (SELECT MAX(score_date) FROM investmitra.daily_scores)
    WHERE sd.capex_screen_pass = TRUE
      AND ds.signal IN ('Strong Buy', 'Buy')
      AND cm.market_cap_category IN ('SMALL','MICRO')
    ORDER BY ds.investmitra_score DESC
    LIMIT 15
""")
print(f"{'Company':<30} {'Sector':<18} {'Cap':<6} {'Score':>6} {'Rev3yr%':>8} {'Rev Cr':>10}")
print('-'*80)
for r in cur.fetchall():
    print(f"{str(r[0])[:29]:<30} {str(r[1])[:17]:<18} {str(r[2])[:5]:<6} {float(r[6] or 0):>6.1f} {float(r[3] or 0):>8.1f} {float(r[8] or 0):>10.0f}")
conn.close()
