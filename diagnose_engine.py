import os, sys
sys.path.insert(0, 'scripts')
from dotenv import load_dotenv
load_dotenv('.env.prod')
from kiteconnect import KiteConnect
from intraday_signals import (get_premarket_context, get_intraday_watchlist,
                               get_rvol_baseline, get_key_levels, classify_gap,
                               GAP_THRESHOLDS, get_dynamic_gappers)

kite = KiteConnect(api_key=os.getenv('KITE_API_KEY'))
kite.set_access_token(os.getenv('KITE_ACCESS_TOKEN'))

ctx = get_premarket_context()
long_list, short_list = get_intraday_watchlist(ctx)

print('=== ENGINE DIAGNOSIS ===')
print(f'Long watchlist: {len(long_list)}')
print(f'Short watchlist: {len(short_list)}')

# Check if ATHERENERG, TCS, WIPRO in watchlist
long_syms = {s['symbol'] for s in long_list}
check = ['ATHERENERG','TCS','WIPRO','BAJFINANCE','ICICIBANK','STYLEBAAZA']
print()
print('Dynamic stocks in watchlist?')
for s in check:
    print(f'  {s}: {"YES" if s in long_syms else "NO - needs dynamic scan"}')

# Check dynamic scan
print()
print('Running dynamic scan now...')
existing = long_syms
gappers = get_dynamic_gappers(kite, existing, ctx)
print(f'Dynamic gappers found: {len(gappers)}')
for g in gappers:
    print(f'  {g["symbol"]}: gap {g.get("gap_pct",0):+.2f}% ({g.get("gap_type","?")})')

# Check key issue - are opens captured?
print()
print('Key issue check:')
quotes = kite.quote(['NSE:ATHERENERG','NSE:TCS','NSE:WIPRO'])
for sym, q in quotes.items():
    s = sym.replace('NSE:','')
    ohlc = q.get('ohlc',{})
    open_p = float(ohlc.get('open',0))
    prev   = float(ohlc.get('close',0))
    ltp    = float(q.get('last_price',0))
    gap    = (open_p-prev)/prev*100 if prev else 0
    print(f'  {s}: open={open_p} prev={prev} ltp={ltp} gap={gap:+.2f}%')
