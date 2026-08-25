import psycopg2, os, json
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
conn.autocommit = True
cur = conn.cursor()
cur.execute("SELECT weights FROM investmitra.signal_weights ORDER BY effective_date DESC LIMIT 1")
row = cur.fetchone()
w = row[0] if isinstance(row[0], dict) else json.loads(row[0])
w['gap_threshold_momentum'] = 0.30
w['gap_threshold_afternoon'] = 0.30
w['rvol_min_continuation'] = 1.5
cur.execute("""
    UPDATE investmitra.signal_weights 
    SET weights=%s 
    WHERE effective_date=(SELECT MAX(effective_date) FROM investmitra.signal_weights)
""", (json.dumps(w),))
print('Threshold lowered to 0.30%')
conn.close()
