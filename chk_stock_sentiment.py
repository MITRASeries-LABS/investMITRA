import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("""
    SELECT cm.nse_symbol, cm.company_name,
           COUNT(*) as articles,
           ROUND(AVG(ne.sentiment_score)::numeric, 2) as avg_sentiment,
           SUM(CASE WHEN ne.sentiment_score > 0.3 THEN 1 ELSE 0 END) as positive,
           SUM(CASE WHEN ne.sentiment_score < -0.3 THEN 1 ELSE 0 END) as negative
    FROM investmitra.news_events ne
    JOIN investmitra.company_master cm ON cm.isin = ANY(ne.entities_isin)
    WHERE ne.sentiment_score IS NOT NULL
    GROUP BY cm.nse_symbol, cm.company_name
    ORDER BY articles DESC
    LIMIT 15
""")
print(f'{"Symbol":<15} {"Articles":>8} {"Sentiment":>10} {"Pos":>5} {"Neg":>5}')
print('-'*50)
for r in cur.fetchall():
    print(f'{str(r[0]):<15} {r[2]:>8} {float(r[3] or 0):>+10.2f} {int(r[4]):>5} {int(r[5]):>5}')
conn.close()
