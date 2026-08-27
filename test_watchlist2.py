import os, sys
sys.path.insert(0, 'scripts')
from dotenv import load_dotenv
load_dotenv('.env.prod')
from intraday_signals import get_premarket_context, get_intraday_watchlist

ctx = get_premarket_context()
long_list, short_list = get_intraday_watchlist(ctx)

print(f'LONG candidates: {len(long_list)}')
print(f'SHORT candidates: {len(short_list)}')

# Show keys available
if long_list:
    print(f'Keys: {list(long_list[0].keys())}')

print()
print('Top 15 LONG:')
for s in long_list[:15]:
    cap = s.get('market_cap_category', s.get('cap','?'))
    print(f'  {s["symbol"]:<15} {cap:<6} score:{s["investmitra_score"]:.1f}')

print()
missing = ['DLF','BEL','GLENMARK','COFORGE','SIEMENS','HINDCOPPER']
long_syms = {s['symbol'] for s in long_list}
for sym in missing:
    print(f'  {sym}: {"IN LIST" if sym in long_syms else "MISSING"}')
