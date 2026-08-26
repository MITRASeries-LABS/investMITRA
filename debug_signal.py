import os, sys
sys.path.insert(0, 'scripts')
from dotenv import load_dotenv
load_dotenv('.env.prod')
from kiteconnect import KiteConnect
from intraday_signals import (get_premarket_context, get_intraday_watchlist,
                               get_rvol_baseline, get_key_levels, GAP_THRESHOLDS,
                               classify_gap)

kite = KiteConnect(api_key=os.getenv('KITE_API_KEY'))
kite.set_access_token(os.getenv('KITE_ACCESS_TOKEN'))

ctx = get_premarket_context()
long_list, short_list = get_intraday_watchlist(ctx)

# Check top stocks
symbols = [s['symbol'] for s in long_list[:10]]
quotes = kite.quote([f"NSE:{s}" for s in symbols])

print(f'{"Symbol":<12} {"Prev":>8} {"Open":>8} {"LTP":>8} {"Gap%":>7} {"Thresh":>7} {"Pass?"}')
print('-'*65)

import psycopg2
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()

for sym in symbols:
    q = quotes.get(f"NSE:{sym}", {})
    open_p = float(q.get("ohlc", {}).get("open", 0))
    prev   = float(q.get("ohlc", {}).get("close", 0))
    ltp    = float(q.get("last_price", 0))
    gap    = (open_p - prev) / prev * 100 if prev > 0 else 0

    # Get cap
    stock = next((s for s in long_list if s['symbol']==sym), {})
    cap   = stock.get('market_cap_category','MID')
    thresh = GAP_THRESHOLDS.get('momentum', 0.30)
    if cap in ('MICRO','SMALL'): thresh *= 0.7

    gap_type, _ = classify_gap(gap, 1000000, 500000)
    above_vwap = ltp >= open_p * 0.998
    passes = abs(gap) > thresh and gap > 0 and above_vwap and gap_type != 'exhaustion' and gap_type != 'fade_risk'

    print(f'{sym:<12} {prev:>8.2f} {open_p:>8.2f} {ltp:>8.2f} {gap:>+7.2f}% {thresh:>7.2f}% {"YES" if passes else "NO"} {gap_type}')

conn.close()
