import psycopg2, os, json
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
conn.autocommit = True
cur = conn.cursor()

cur.execute("SELECT weights FROM investmitra.signal_weights ORDER BY effective_date DESC LIMIT 1")
w = cur.fetchone()[0]
if isinstance(w, str): w = json.loads(w)

# Remove restrictions Opus wrongly applied
w['skip_fade_risk']      = False  # We handle this in classify_gap now
w['skip_choppy_session'] = False  # Choppy session has afternoon signals
w['gap_threshold_momentum'] = 0.30  # Already fixed manually
w['rvol_min_continuation']  = 1.5   # Already fixed manually

cur.execute("""
    UPDATE investmitra.signal_weights 
    SET weights=%s 
    WHERE effective_date=(SELECT MAX(effective_date) FROM investmitra.signal_weights)
""", (json.dumps(w),))
print('Weights updated:')
print(f'  skip_fade_risk: False')
print(f'  skip_choppy_session: False')
print(f'  gap_threshold_momentum: 0.30')
print(f'  rvol_min_continuation: 1.5')
conn.close()
