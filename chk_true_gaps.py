import yfinance as yf
import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')

# Get our watchlist symbols
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("""
    SELECT DISTINCT cm.nse_symbol
    FROM investmitra.daily_scores ds
    JOIN investmitra.company_master cm ON ds.isin=cm.isin
    WHERE ds.score_date = (SELECT MAX(score_date) FROM investmitra.daily_scores)
      AND ds.investmitra_score >= 60
      AND cm.market_cap_category IN ('MID','LARGE','SMALL')
      AND cm.nse_symbol IS NOT NULL
    LIMIT 30
""")
symbols = [r[0] for r in cur.fetchall()]
conn.close()

print(f'Checking {len(symbols)} stocks for true gaps today...')
print(f'{"Symbol":<15} {"Prev Close":>10} {"Open":>8} {"True Gap":>9} {"High":>8} {"Low":>8} {"Signal?"}')
print('-'*75)

signals = []
for sym in symbols:
    try:
        tk = yf.Ticker(f'{sym}.NS')
        hist = tk.history(period='2d', interval='1d')
        if len(hist) < 2: continue
        prev_close = float(hist['Close'].iloc[-2])
        today_open = float(hist['Open'].iloc[-1])
        today_high = float(hist['High'].iloc[-1])
        today_low  = float(hist['Low'].iloc[-1])
        true_gap   = (today_open - prev_close) / prev_close * 100
        signal = '?? LONG' if true_gap > 0.3 else '?? SHORT' if true_gap < -0.3 else '?'
        if abs(true_gap) > 0.3:
            signals.append((sym, true_gap, today_open, today_high, today_low))
        print(f'{sym:<15} {prev_close:>10.2f} {today_open:>8.2f} {true_gap:>+8.2f}% {today_high:>8.2f} {today_low:>8.2f}  {signal}')
    except: pass

print(f'\nStocks with genuine gap >0.3%: {len(signals)}')
for s in signals:
    print(f'  {s[0]}: {s[1]:+.2f}%')
