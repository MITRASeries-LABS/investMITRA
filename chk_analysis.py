import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()

print('=== TRADE LOG ===')
cur.execute("SELECT trade_date, symbol, direction, net_pnl, outcome FROM investmitra.trade_log ORDER BY created_at DESC LIMIT 10")
for r in cur.fetchall():
    print(f'  {r[0]} {r[1]} {r[2]} net=?{float(r[3]):.0f} ({r[4]})')

print('\n=== TRADE INSIGHTS ===')
cur.execute("SELECT trade_date, symbol, outcome, LEFT(analysis,80) FROM investmitra.trade_insights ORDER BY created_at DESC LIMIT 5")
for r in cur.fetchall():
    print(f'  {r[0]} {r[1]} ({r[2]}): {r[3]}')

print('\n=== SIGNAL WEIGHTS ===')
cur.execute("SELECT effective_date, updated_by, trade_count FROM investmitra.signal_weights ORDER BY effective_date DESC LIMIT 3")
for r in cur.fetchall():
    print(f'  {r[0]} updated_by={r[1]} trades={r[2]}')

print('\n=== INTRADAY PNL ===')
cur.execute("SELECT trade_date, trades, net_pnl, win_trades, loss_trades FROM investmitra.intraday_pnl ORDER BY trade_date DESC LIMIT 5")
for r in cur.fetchall():
    print(f'  {r[0]} trades={r[1]} net=?{float(r[2]):.0f} W:{r[3]} L:{r[4]}')

conn.close()
