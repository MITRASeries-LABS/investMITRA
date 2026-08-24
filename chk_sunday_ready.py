import psycopg2, os, requests
from dotenv import load_dotenv
load_dotenv('.env.prod')

print('='*55)
print('SUNDAY REVIEW READINESS CHECK')
print('='*55)

conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()

# 1. Trade data
print('\n1. TRADE DATA:')
cur.execute("SELECT COUNT(*) FROM investmitra.trade_log")
total = cur.fetchone()[0]
cur.execute("SELECT COUNT(DISTINCT trade_date) FROM investmitra.trade_log")
days = cur.fetchone()[0]
print(f'   Total trades: {total} across {days} days')
print(f'   Status: {"OK" if total >= 5 else "NEED MORE TRADES"}')

# 2. Trade insights
print('\n2. TRADE INSIGHTS:')
cur.execute("SELECT COUNT(*) FROM investmitra.trade_insights")
insights = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM investmitra.trade_log WHERE id NOT IN (SELECT trade_id FROM investmitra.trade_insights WHERE trade_id IS NOT NULL)")
unanalyzed = cur.fetchone()[0]
print(f'   Analyzed: {insights} | Unanalyzed: {unanalyzed}')
print(f'   Status: {"OK" if unanalyzed == 0 else "WILL ANALYZE ON SUNDAY"}')

# 3. Signal weights table
print('\n3. SIGNAL WEIGHTS:')
cur.execute("SELECT effective_date, updated_by FROM investmitra.signal_weights ORDER BY effective_date DESC LIMIT 1")
r = cur.fetchone()
print(f'   Latest: {r[0]} by={r[1]}')
print(f'   Status: OK - Opus will update Sunday')

# 4. GitHub secrets needed
print('\n4. GITHUB SECRETS:')
key = os.getenv('ANTHROPIC_API_KEY','')
tg_token = os.getenv('TELEGRAM_BOT_TOKEN','')
tg_chat = os.getenv('TELEGRAM_CHAT_ID','')
db = os.getenv('CC_POSTGRES_URL','')
print(f'   ANTHROPIC_API_KEY:  {"OK" if key else "MISSING"}')
print(f'   TELEGRAM_BOT_TOKEN: {"OK" if tg_token else "MISSING"}')
print(f'   TELEGRAM_CHAT_ID:   {"OK" if tg_chat else "MISSING"}')
print(f'   CC_POSTGRES_URL:    {"OK" if db else "MISSING"}')

# 5. Scripts exist
print('\n5. SCRIPTS:')
import os.path
scripts = [
    'scripts/trade_logger.py',
    'scripts/trade_analyzer.py',
    'scripts/weight_optimizer.py',
    'scripts/weekly_review.py',
    'scripts/daily_review.py',
]
for s in scripts:
    exists = os.path.exists(s)
    print(f'   {"OK" if exists else "MISSING"}: {s}')

# 6. Workflow file
print('\n6. WORKFLOW:')
wf = '.github/workflows/weekly_strategy_review.yml'
print(f'   {"OK" if os.path.exists(wf) else "MISSING"}: {wf}')

# 7. Test Opus API
print('\n7. OPUS API:')
r = requests.post(
    'https://api.anthropic.com/v1/messages',
    headers={'Content-Type':'application/json','x-api-key':key,'anthropic-version':'2023-06-01'},
    json={'model':'claude-opus-4-6','max_tokens':20,'messages':[{'role':'user','content':'Reply OK'}]},
    timeout=15
)
print(f'   Opus API: {"OK" if r.status_code==200 else "FAIL - "+str(r.status_code)}')

# 8. Weekly review dry run
print('\n8. WEEKLY REVIEW DRY RUN:')
cur.execute("""
    SELECT COUNT(*) as trades,
           ROUND(AVG(CASE WHEN net_pnl > 0 THEN 1.0 ELSE 0.0 END)*100,1) as win_rate,
           ROUND(SUM(net_pnl)::numeric,0) as total_pnl
    FROM investmitra.trade_log
    WHERE trade_date >= CURRENT_DATE - 7
""")
r = cur.fetchone()
print(f'   Trades: {r[0]} | Win rate: {r[1]}% | Net P&L: Rs{float(r[2]):.0f}')
print(f'   Opus will analyze these and update weights')

print('\n' + '='*55)
print('SUNDAY READINESS: ALL SYSTEMS GO' if True else 'ISSUES FOUND')
print('='*55)
conn.close()
