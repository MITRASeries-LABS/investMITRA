import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
conn.autocommit = True
cur = conn.cursor()

# Keep only 30 days of price data (was 90)
cur.execute("DELETE FROM investmitra.equity_prices WHERE trade_date < CURRENT_DATE - INTERVAL '30 days'")
print(f'equity_prices: deleted {cur.rowcount} rows')

# Keep only 14 days of market indices
cur.execute("DELETE FROM investmitra.market_indices WHERE fetch_date < CURRENT_DATE - INTERVAL '14 days'")
print(f'market_indices: deleted {cur.rowcount} rows')

# Keep only 14 days of global indices
cur.execute("DELETE FROM investmitra.global_indices WHERE fetch_date < CURRENT_DATE - INTERVAL '14 days'")
print(f'global_indices: deleted {cur.rowcount} rows')

# Keep only 30 days of screener signals
cur.execute("DELETE FROM investmitra.screener_signals WHERE signal_date < CURRENT_DATE - INTERVAL '30 days'")
print(f'screener_signals: deleted {cur.rowcount} rows')

# Keep only 30 days of NSE announcements
cur.execute("DELETE FROM investmitra.nse_announcements WHERE fetch_date < CURRENT_DATE - INTERVAL '30 days'")
print(f'nse_announcements: deleted {cur.rowcount} rows')

# Keep only 30 days of SEBI updates
cur.execute("DELETE FROM investmitra.sebi_updates WHERE fetch_date < CURRENT_DATE - INTERVAL '30 days'")
print(f'sebi_updates: deleted {cur.rowcount} rows')

# Vacuum to reclaim space
cur.execute("VACUUM ANALYZE")
print('Vacuum complete')
conn.close()
print('Cleanup done!')
