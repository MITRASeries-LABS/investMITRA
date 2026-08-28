import os
from dotenv import load_dotenv
load_dotenv('.env.prod')
from kiteconnect import KiteConnect
kite = KiteConnect(api_key=os.getenv('KITE_API_KEY'))
kite.set_access_token(os.getenv('KITE_ACCESS_TOKEN'))

symbols = ['TCS','WIPRO','ATHERENERG','BAJFINANCE','ICICIBANK',
           'STYLEBAAZA','KALYANKJIL','HSCL','COFORGE','PERSISTENT',
           'MCX','HINDZINC','EMMVEE','ATLANTAELE','HAL']

quotes = kite.quote([f"NSE:{s}" for s in symbols])
print(f'{"Symbol":<15} {"Open":>8} {"High":>8} {"Low":>8} {"LTP":>8} {"Gap%":>7} {"MaxUp%":>8} {"MaxDn%":>8}')
print('-'*80)

results = []
for sym in symbols:
    q = quotes.get(f"NSE:{sym}", {})
    ohlc = q.get("ohlc", {})
    prev  = float(ohlc.get("close", 0))
    open_p= float(ohlc.get("open", 0))
    high  = float(ohlc.get("high", 0))
    low   = float(ohlc.get("low", 0))
    ltp   = float(q.get("last_price", 0))
    if not prev or not open_p: continue
    gap    = (open_p - prev) / prev * 100
    max_up = (high - open_p) / open_p * 100
    max_dn = (open_p - low) / open_p * 100
    results.append((sym, prev, open_p, high, low, ltp, gap, max_up, max_dn))

results.sort(key=lambda x: abs(x[6]), reverse=True)
for r in results:
    sym,prev,open_p,high,low,ltp,gap,max_up,max_dn = r
    best = 'LONG WIN' if gap>0.3 and max_up>2 else 'SHORT WIN' if gap<-0.3 and max_dn>2 else ''
    print(f'{sym:<15} {open_p:>8.2f} {high:>8.2f} {low:>8.2f} {ltp:>8.2f} {gap:>+7.2f}% {max_up:>+8.2f}% {max_dn:>+8.2f}%  {best}')
