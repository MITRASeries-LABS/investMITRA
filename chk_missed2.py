import yfinance as yf
import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')

# Get correct column name
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='investmitra' AND table_name='daily_scores' LIMIT 10")
cols = [r[0] for r in cur.fetchall()]
print('Columns:', cols)

cur.execute("""
    SELECT DISTINCT cm.nse_symbol 
    FROM investmitra.daily_scores ds
    JOIN investmitra.company_master cm ON ds.isin = cm.isin
    WHERE ds.score_date = '2026-08-27'
    AND ds.investmitra_score >= 55
    LIMIT 60
""")
symbols = [r[0] for r in cur.fetchall()]
conn.close()

print(f'Checking {len(symbols)} stocks...')
print(f'{"Symbol":<15} {"Prev":>8} {"Open":>8} {"High":>8} {"Low":>8} {"Gap%":>7} {"MaxMove%":>9} {"Dir"}')
print('-'*75)

winners = []
for sym in symbols:
    try:
        tk = yf.Ticker(f'{sym}.NS')
        hist = tk.history(period='2d', interval='1d')
        if len(hist) < 2: continue
        prev   = float(hist.Close.iloc[-2])
        open_p = float(hist.Open.iloc[-1])
        high   = float(hist.High.iloc[-1])
        low    = float(hist.Low.iloc[-1])
        gap    = (open_p - prev) / prev * 100
        if gap > 0:
            max_move = (high - open_p) / open_p * 100
            direction = 'LONG'
        else:
            max_move = (open_p - low) / open_p * 100
            direction = 'SHORT'
        if abs(gap) > 0.3 and abs(max_move) > 1.0:
            winners.append((sym, prev, open_p, high, low, gap, max_move, direction))
    except: pass

winners.sort(key=lambda x: abs(x[6]), reverse=True)
for w in winners[:20]:
    sym, prev, open_p, high, low, gap, max_move, direction = w
    print(f'{sym:<15} {prev:>8.2f} {open_p:>8.2f} {high:>8.2f} {low:>8.2f} {gap:>+7.2f}% {max_move:>+8.2f}%  {direction}')
