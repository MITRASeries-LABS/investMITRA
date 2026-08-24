import os, sys
sys.path.insert(0, 'scripts')
from dotenv import load_dotenv
load_dotenv('.env.prod')
from intraday_signals import get_premarket_context, get_intraday_watchlist
ctx = get_premarket_context()
long_list, short_list = get_intraday_watchlist(ctx)
print(f'LONG candidates: {len(long_list)}')
print(f'SHORT candidates: {len(short_list)}')
for s in long_list[:5]:
    print(f'  {s["symbol"]} score={s["investmitra_score"]:.1f}')
