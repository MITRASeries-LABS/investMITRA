import os, sys
from dotenv import load_dotenv
load_dotenv('.env.prod')
from kiteconnect import KiteConnect

kite = KiteConnect(api_key=os.getenv('KITE_API_KEY'))
kite.set_access_token(os.getenv('KITE_ACCESS_TOKEN'))

# Check today's moves for watchlist stocks
symbols = [
    'MCX','HAL','BEL','OFSS','HINDZINC','HINDPETRO','EMMVEE',
    'TIPSMUSIC','KRBL','NAM-INDIA','PERSISTENT','GLENMARK',
    'NATIONALUM','COFORGE','HCLTECH','HINDCOPPER','ATLANTAELE',
    'CRIZAC','GNFC','THYROCARE','ANANDRATHI','GILLETTE',
    'PIDILITIND','HYUNDAI','EICHERMOT','SIEMENS','DLF','MARICO'
]

quotes = kite.quote([f"NSE:{s}" for s in symbols])

print(f'{"Symbol":<15} {"Prev":>8} {"Open":>8} {"High":>8} {"Low":>8} {"Gap%":>7} {"MaxUp%":>8} {"MaxDn%":>8}')
print('-'*80)

opportunities = []
for sym in symbols:
    q = quotes.get(f"NSE:{sym}", {})
    ohlc  = q.get("ohlc", {})
    prev  = float(ohlc.get("close", 0))
    open_p= float(ohlc.get("open", 0))
    high  = float(ohlc.get("high", 0))
    low   = float(ohlc.get("low", 0))
    if not prev or not open_p: continue
    gap     = (open_p - prev) / prev * 100
    max_up  = (high - open_p) / open_p * 100
    max_dn  = (open_p - low) / open_p * 100
    opportunities.append((sym, prev, open_p, high, low, gap, max_up, max_dn))

# Sort by gap size
opportunities.sort(key=lambda x: abs(x[5]), reverse=True)
for o in opportunities:
    sym, prev, open_p, high, low, gap, max_up, max_dn = o
    missed = ''
    if gap > 0.3 and max_up > 1.5: missed = 'MISSED LONG'
    if gap < -0.3 and max_dn > 1.5: missed = 'MISSED SHORT'
    print(f'{sym:<15} {prev:>8.2f} {open_p:>8.2f} {high:>8.2f} {low:>8.2f} {gap:>+7.2f}% {max_up:>+8.2f}% {max_dn:>+8.2f}%  {missed}')
