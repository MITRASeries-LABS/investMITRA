import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("""
    SELECT company_name, nse_symbol, sector,
           investmitra_score, signal, ta_decision, both_agree,
           momentum_score, financial_health_score, management_quality_score
    FROM investmitra.top_picks
    WHERE pick_date = (SELECT MAX(pick_date) FROM investmitra.top_picks)
    ORDER BY rank
""")
rows = cur.fetchall()
print(f"{'Company':<35} {'Symbol':<12} {'Score':>6} {'investMITRA':<12} {'TradingAgents':<15} {'Agree'}")
print('-'*90)
for r in rows:
    agree = '?' if r[6] else '??'
    print(f"{str(r[0]):<35} {str(r[1]):<12} {float(r[3]):>6.1f} {str(r[4]):<12} {str(r[5]):<15} {agree}")
conn.close()
