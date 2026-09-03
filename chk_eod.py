import os
from dotenv import load_dotenv
load_dotenv('.env.prod')
from kiteconnect import KiteConnect
kite = KiteConnect(api_key=os.getenv('KITE_API_KEY'))
kite.set_access_token(os.getenv('KITE_ACCESS_TOKEN'))

stocks = [
    ('APCOTEXIND', 635.25, 39),
    ('AGI',        794.70, 31),
    ('KIRIINDUS',  515.90, 48),
    ('ATLANTAELE', 1842.30, 13),
    ('TIMEX',      666.65, 37),
]

quotes = kite.quote([f"NSE:{s[0]}" for s in stocks])
print(f'{"Symbol":<15} {"Entry":>8} {"High":>8} {"Close":>8} {"MaxMove%":>9} {"Result"}')
print('-'*70)

total = 0
for sym, entry, qty in stocks:
    q     = quotes.get(f"NSE:{sym}", {})
    ohlc  = q.get("ohlc", {})
    high  = float(ohlc.get("high", 0))
    close = float(q.get("last_price", 0))
    max_move = (high - entry) / entry * 100
    pnl = (close - entry) * qty - 80
    total += pnl
    result = "WIN" if close > entry else "LOSS"
    print(f'{sym:<15} {entry:>8.2f} {high:>8.2f} {close:>8.2f} {max_move:>+8.1f}%  {result} ?{pnl:+.0f}')

print(f'\nTotal net P&L: ?{total:.0f}')
