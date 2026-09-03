import os
from dotenv import load_dotenv
load_dotenv('.env.prod')
from kiteconnect import KiteConnect
kite = KiteConnect(api_key=os.getenv('KITE_API_KEY'))
kite.set_access_token(os.getenv('KITE_ACCESS_TOKEN'))

positions = [
    ('SOTL',       745.00,  784.00, 706.00, 33),
    ('QPOWER',    1455.10, 1535.00,1374.00, 17),
    ('PERSISTENT',5770.50, 5944.00,5597.00,  4),
    ('CONCORDBIO',1486.50, 1564.00,1408.00, 16),
]

quotes = kite.quote([f"NSE:{p[0]}" for p in positions])
print(f'{"Symbol":<15} {"Entry":>8} {"LTP":>8} {"Target":>8} {"P&L":>8} {"Status"}')
print('-'*70)

total = 0
for sym, entry, target, stop, qty in positions:
    q   = quotes.get(f"NSE:{sym}", {})
    ltp = float(q.get("last_price", 0))
    pnl = (ltp - entry) * qty
    total += pnl
    if ltp >= target: status = "TARGET!"
    elif ltp <= stop: status = "STOP!"
    elif ltp > entry: status = "Profit"
    else:             status = "Loss"
    print(f'{sym:<15} {entry:>8.2f} {ltp:>8.2f} {target:>8.2f} {pnl:>+8.0f}  {status}')

print(f'\nUnrealized P&L: Rs{total:+.0f}')
