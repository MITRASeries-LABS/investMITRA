import os, sys
sys.path.insert(0, 'scripts')
from dotenv import load_dotenv
load_dotenv('.env.prod')
from intraday_signals import get_premarket_context, get_intraday_watchlist

ctx = get_premarket_context()
long_list, short_list = get_intraday_watchlist(ctx)

print(f'LONG candidates: {len(long_list)}')
print(f'SHORT candidates: {len(short_list)}')
print()

# Check cap distribution
from collections import Counter
caps = Counter(s['market_cap_category'] for s in long_list)
print('Cap distribution:')
for cap, count in sorted(caps.items()):
    print(f'  {cap}: {count}')

print()
print('Top 10 LONG:')
for s in long_list[:10]:
    print(f'  {s["symbol"]:<15} {s["market_cap_category"]:<6} score:{s["investmitra_score"]:.1f}')

# Check if DLF, BEL, GLENMARK now in list
print()
missing = ['DLF','BEL','GLENMARK','COFORGE','SIEMENS','HINDCOPPER']
long_syms = {s['symbol'] for s in long_list}
for sym in missing:
    status = 'IN LIST' if sym in long_syms else 'MISSING'
    print(f'  {sym}: {status}')
