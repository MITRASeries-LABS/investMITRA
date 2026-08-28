import os
from dotenv import load_dotenv
load_dotenv('.env.prod')
from kiteconnect import KiteConnect

kite = KiteConnect(api_key=os.getenv('KITE_API_KEY'))
kite.set_access_token(os.getenv('KITE_ACCESS_TOKEN'))

# All stocks we should have been watching today
symbols = [
    # Our signals
    'COFORGE','PERSISTENT',
    # Dynamic scan found
    'ATHERENERG','TCS','WIPRO','HCLTECH','TECHM','INFY',
    'BAJFINANCE','ICICIBANK','TATASTEEL','ULTRACEMCO',
    # Watchlist stocks
    'MCX','HINDZINC','EMMVEE','ATLANTAELE','HAL',
    'OFSS','GLAXO','ANTHEM','NAM-INDIA','BEL',
    'HINDCOPPER','GLENMARK','SIEMENS','EICHERMOT',
]

quotes = kite.quote([f"NSE:{s}" for s in symbols])

print(f'{"Symbol":<15} {"Open":>8} {"High":>8} {"Low":>8} {"Close":>8} {"Gap%":>7} {"MaxUp%":>8} {"Result"}')
print('-'*85)

hits = []
misses = []

for sym in symbols:
    q = quotes.get(f"NSE:{sym}", {})
    ohlc  = q.get("ohlc", {})
    prev  = float(ohlc.get("close", 0))
    open_p= float(ohlc.get("open", 0))
    high  = float(ohlc.get("high", 0))
    low   = float(ohlc.get("low", 0))
    close = float(q.get("last_price", 0))
    if not prev or not open_p: continue

    gap    = (open_p - prev) / prev * 100
    max_up = (high - open_p) / open_p * 100
    max_dn = (open_p - low) / open_p * 100

    if gap > 0.3 and max_up > 1.5:
        result = f'MISSED LONG +{max_up:.1f}%'
        misses.append((sym, gap, max_up, 'LONG'))
    elif gap < -0.3 and max_dn > 1.5:
        result = f'MISSED SHORT +{max_dn:.1f}%'
        misses.append((sym, gap, max_dn, 'SHORT'))
    elif sym in ['COFORGE','PERSISTENT']:
        result = f'TRADED (gap {gap:+.1f}%)'
        hits.append((sym, gap, max_up))
    else:
        result = f'gap {gap:+.1f}%'

    print(f'{sym:<15} {open_p:>8.2f} {high:>8.2f} {low:>8.2f} {close:>8.2f} {gap:>+7.2f}% {max_up:>+8.2f}%  {result}')

print()
print(f'TRADED: {len(hits)} | MISSED: {len(misses)}')
print()
print('TOP MISSED OPPORTUNITIES:')
misses.sort(key=lambda x: x[2], reverse=True)
for sym, gap, move, direction in misses[:5]:
    print(f'  {sym}: gap {gap:+.2f}% ? max {direction} move {move:+.1f}%')
