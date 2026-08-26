import os, sys
from dotenv import load_dotenv
load_dotenv('.env.prod')
from kiteconnect import KiteConnect
kite = KiteConnect(api_key=os.getenv('KITE_API_KEY'))
kite.set_access_token(os.getenv('KITE_ACCESS_TOKEN'))

symbols = ['MCX','HAL','BEL','EMMVEE','OFSS','TIPSMUSIC','HINDZINC','HINDPETRO']
quotes = kite.quote([f"NSE:{s}" for s in symbols])
print(f'{"Symbol":<15} {"Open":>8} {"LTP":>8} {"Gap%":>8} {"Signal?"}')
print('-'*55)
for sym in symbols:
    q = quotes.get(f"NSE:{sym}", {})
    open_p = float(q.get("ohlc", {}).get("open", 0))
    prev = float(q.get("ohlc", {}).get("close", 0))
    ltp = float(q.get("last_price", 0))
    gap = (open_p - prev) / prev * 100 if prev > 0 else 0
    signal = "LONG?" if gap > 0.30 else "SHORT?" if gap < -0.30 else "no gap"
    print(f'{sym:<15} {open_p:>8.2f} {ltp:>8.2f} {gap:>+8.2f}%  {signal}')
