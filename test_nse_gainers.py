import os, sys
sys.path.insert(0, 'scripts')
from dotenv import load_dotenv
load_dotenv('.env.prod')
from intraday_signals import get_nse_gainers_losers

print('Testing NSE gainers/losers...')
gainers, losers = get_nse_gainers_losers()
print(f'Gainers ({len(gainers)}): {gainers[:10]}')
print(f'Losers ({len(losers)}): {losers[:10]}')
