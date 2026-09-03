import os
from dotenv import load_dotenv
load_dotenv('.env.prod')
from kiteconnect import KiteConnect
kite = KiteConnect(api_key=os.getenv('KITE_API_KEY'))
kite.set_access_token(os.getenv('KITE_ACCESS_TOKEN'))
symbols = ['MCX','HINDZINC','TCS','HCLTECH','WIPRO','INFY','ATLANTAELE','SOTL','COFORGE','PERSISTENT','HAL','BEL']
quotes = kite.quote([f'NSE:{s}' for s in symbols])
print(f'Symbol          Open     High    Close    Gap%  MaxMove%')
print('-'*65)
for sym in symbols:
    q = quotes.get(f'NSE:{sym}', {})
    ohlc = q.get('ohlc', {})
    prev = float(ohlc.get('close', 0))
    open_p = float(ohlc.get('open', 0))
    high = float(ohlc.get('high', 0))
    close = float(q.get('last_price', 0))
    if not prev or not open_p: continue
    gap = (open_p - prev) / prev * 100
    move = (high - open_p) / open_p * 100
    missed = 'MISSED!' if gap > 0.3 and move > 1.5 else ''
    print(f'{sym:<15} {open_p:>8.2f} {high:>8.2f} {close:>8.2f} {gap:>+6.2f}% {move:>+8.2f}%  {missed}')
