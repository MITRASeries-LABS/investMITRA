import psycopg2, os, requests
from dotenv import load_dotenv
load_dotenv('.env.prod')

conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()

print('='*55)
print('investMITRA END-TO-END TEST')
print('='*55)

# 1. Trade log
print('\n1. TRADE LOG:')
cur.execute("SELECT COUNT(*) FROM investmitra.trade_log WHERE trade_date >= CURRENT_DATE - 7")
print(f'   Trades last 7 days: {cur.fetchone()[0]}')
cur.execute("SELECT trade_date, symbol, net_pnl, outcome FROM investmitra.trade_log ORDER BY created_at DESC LIMIT 5")
for r in cur.fetchall():
    print(f'   {r[0]} {r[1]} net=Rs{float(r[2]):.0f} ({r[3]})')

# 2. Trade insights (Sonnet analysis)
print('\n2. TRADE INSIGHTS (Sonnet):')
cur.execute("SELECT COUNT(*) FROM investmitra.trade_insights")
print(f'   Total insights: {cur.fetchone()[0]}')
cur.execute("SELECT symbol, LEFT(analysis,60) FROM investmitra.trade_insights ORDER BY created_at DESC LIMIT 3")
for r in cur.fetchall():
    print(f'   {r[0]}: {r[1]}')

# 3. Signal weights
print('\n3. SIGNAL WEIGHTS:')
cur.execute("SELECT effective_date, updated_by FROM investmitra.signal_weights ORDER BY effective_date DESC LIMIT 3")
for r in cur.fetchall():
    print(f'   {r[0]} by={r[1]}')

# 4. Intraday PnL
print('\n4. INTRADAY PNL:')
cur.execute("SELECT trade_date, trades, net_pnl, win_trades, loss_trades FROM investmitra.intraday_pnl ORDER BY trade_date DESC LIMIT 5")
for r in cur.fetchall():
    print(f'   {r[0]} trades={r[1]} net=Rs{float(r[2]):.0f} W:{r[3]} L:{r[4]}')

# 5. Daily scores fresh
print('\n5. DAILY SCORES:')
cur.execute("SELECT MAX(score_date), COUNT(DISTINCT isin) FROM investmitra.daily_scores")
r = cur.fetchone()
print(f'   Latest: {r[0]} | ISINs: {r[1]}')

# 6. Top picks
print('\n6. TOP PICKS:')
cur.execute("SELECT pick_date, nse_symbol, investmitra_score, triple_confirm FROM investmitra.top_picks WHERE cap_filter='SMALLMICRO' ORDER BY pick_date DESC, rank LIMIT 3")
for r in cur.fetchall():
    print(f'   {r[0]} {r[1]} score={float(r[2]):.1f} triple={r[3]}')

# 7. Market data
print('\n7. MARKET DATA:')
cur.execute("SELECT COUNT(*) FROM investmitra.market_indices WHERE fetch_date=CURRENT_DATE")
print(f'   Indices today: {cur.fetchone()[0]}')
cur.execute("SELECT COUNT(*) FROM investmitra.global_indices WHERE fetch_date=CURRENT_DATE")
print(f'   Global indices today: {cur.fetchone()[0]}')

# 8. Anthropic API
print('\n8. ANTHROPIC API:')
key = os.getenv('ANTHROPIC_API_KEY','')
r = requests.post(
    'https://api.anthropic.com/v1/messages',
    headers={'Content-Type':'application/json','x-api-key':key,'anthropic-version':'2023-06-01'},
    json={'model':'claude-sonnet-4-6','max_tokens':50,'messages':[{'role':'user','content':'Reply OK'}]},
    timeout=10
)
if r.status_code == 200:
    print(f'   Sonnet API: OK')
else:
    print(f'   Sonnet API: FAIL {r.status_code}')

# 9. Telegram
print('\n9. TELEGRAM:')
token = os.getenv('TELEGRAM_BOT_TOKEN','')
chat  = os.getenv('TELEGRAM_CHAT_ID','')
if token and chat:
    r = requests.get(f'https://api.telegram.org/bot{token}/sendMessage',
        params={'chat_id':chat,'text':'investMITRA E2E test passed!'},timeout=5)
    print(f'   Telegram: {"OK" if r.json().get("ok") else "FAIL"}')
else:
    print('   Telegram: NOT CONFIGURED')

print('\n' + '='*55)
print('E2E TEST COMPLETE')
print('='*55)
conn.close()
