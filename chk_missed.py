import yfinance as yf
import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')

# Get today's watchlist stocks
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("""
    SELECT DISTINCT nse_symbol 
    FROM investmitra.daily_scores 
    WHERE score_date = '2026-08-27'
    AND investmitra_score >= 55
    LIMIT 50
""")
symbols = [r[0] for r in cur.fetchall()]
conn.close()

print(f'Checking {len(symbols)} stocks for today Aug 27...')
print(f'{"Symbol":<15} {"Open":>8} {"High":>8} {"Low":>8} {"Close":>8} {"Gap%":>7} {"MaxMove%":>9} {"Direction"}')
print('-'*80)

winners = []
for sym in symbols:
    try:
        tk = yf.Ticker(f'{sym}.NS')
        hist = tk.history(period='2d', interval='1d')
        if len(hist) < 2: continue
        prev  = float(hist.Close.iloc[-2])
        open_p= float(hist.Open.iloc[-1])
        high  = float(hist.High.iloc[-1])
        low   = float(hist.Low.iloc[-1])
        close = float(hist.Close.iloc[-1])
        gap   = (open_p - prev) / prev * 100
        
        # Max move from open
        if gap > 0:
            max_move = (high - open_p) / open_p * 100
            direction = 'LONG'
        else:
            max_move = (open_p - low) / open_p * 100
            direction = 'SHORT'
        
        if abs(gap) > 0.3 and abs(max_move) > 1.0:
            winners.append((sym, open_p, high, low, close, gap, max_move, direction))
    except: pass

winners.sort(key=lambda x: abs(x[6]), reverse=True)
for w in winners[:20]:
    sym, open_p, high, low, close, gap, max_move, direction = w
    print(f'{sym:<15} {open_p:>8.2f} {high:>8.2f} {low:>8.2f} {close:>8.2f} {gap:>+7.2f}% {max_move:>+8.2f}%  {direction}')
