import os, sys
sys.path.insert(0, 'scripts')
from dotenv import load_dotenv
load_dotenv('.env.prod')
from kiteconnect import KiteConnect
api_key = os.getenv('KITE_API_KEY')
token = os.getenv('KITE_ACCESS_TOKEN')
kite = KiteConnect(api_key=api_key)
kite.set_access_token(token)

# Check live prices and gaps for top watchlist stocks
import psycopg2
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("SELECT nse_symbol, prev_close FROM investmitra.equity_prices WHERE trade_date=(SELECT MAX(trade_date) FROM investmitra.equity_prices) AND nse_symbol IN ('MCX','HAL','BEL','HINDCOPPER','EMMVEE','OFSS','TIPSMUSIC','ATLANTAELE') ")
prev_closes = {r[0]: float(r[1]) for r in cur.fetchall()}
conn.close()

symbols = list(prev_closes.keys())
quotes = kite.quote([f"NSE:{s}" for s in symbols])
print(f'{"Symbol":<15} {"Prev":>8} {"Open":>8} {"LTP":>8} {"Gap%":>8} {"Signal?"}')
print('-'*65)
for sym in symbols:
    q = quotes.get(f"NSE:{sym}", {})
    prev = prev_closes.get(sym, 0)
    open_p = float(q.get("ohlc", {}).get("open", 0))
    ltp = float(q.get("last_price", 0))
    gap = (open_p - prev) / prev * 100 if prev > 0 else 0
    signal = "LONG?" if gap > 0.30 else "SHORT?" if gap < -0.30 else "no gap"
    print(f'{sym:<15} {prev:>8.2f} {open_p:>8.2f} {ltp:>8.2f} {gap:>+8.2f}%  {signal}')
