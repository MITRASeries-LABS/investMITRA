import os
from dotenv import load_dotenv
load_dotenv('.env.prod')
from kiteconnect import KiteConnect
kite = KiteConnect(api_key=os.getenv('KITE_API_KEY'))
kite.set_access_token(os.getenv('KITE_ACCESS_TOKEN'))
q = kite.quote(['NSE:SOTL'])['NSE:SOTL']
ohlc = q.get('ohlc',{})
print(f'SOTL: open={ohlc.get("open")} high={ohlc.get("high")} low={ohlc.get("low")} close={q.get("last_price")}')
entry = 744.90
close = float(q.get('last_price',0))
pnl = (close - entry) * 33 - 80
print(f'Entry: {entry} | Close: {close} | Net P&L: Rs{pnl:.0f}')
