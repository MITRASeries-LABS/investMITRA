import os
from dotenv import load_dotenv
load_dotenv('.env.prod')

# Check 1: Kite token
token = os.getenv('KITE_ACCESS_TOKEN')
print(f'Kite token: {"? present" if token else "? MISSING ? run kite_login.py"}')

# Check 2: Neon connection
import psycopg2
try:
    conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'), connect_timeout=5)
    cur = conn.cursor()
    cur.execute('SELECT MAX(score_date) FROM investmitra.daily_scores')
    print(f'Neon scores: ? latest {cur.fetchone()[0]}')
    cur.execute('SELECT COUNT(*) FROM investmitra.equity_prices WHERE trade_date >= CURRENT_DATE - INTERVAL 30 * interval 1 second * 86400')
    conn.close()
except Exception as e:
    print(f'Neon: ? {e}')

# Check 3: kiteconnect installed
try:
    from kiteconnect import KiteConnect, KiteTicker
    print('kiteconnect: ? installed')
except:
    print('kiteconnect: ? not installed ? pip install kiteconnect')

print('Ready for trading!')
