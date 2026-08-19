import os, sys
sys.path.insert(0, 'scripts')
from dotenv import load_dotenv
load_dotenv('.env.prod')

print('Testing order_manager components...')

# Test 1: Telegram
print('TEST 1: Telegram...')
try:
    from order_manager import notify
    notify('investMITRA test message from order_manager.py')
    print('  PASS - check Telegram for message')
except Exception as e:
    print('  FAIL:', e)

# Test 2: Load signals
print('TEST 2: Load signals...')
try:
    from order_manager import load_todays_signals
    sigs = load_todays_signals()
    print('  Signals loaded:', len(sigs))
    print('  PASS')
except Exception as e:
    print('  FAIL:', e)

# Test 3: Kite connection
print('TEST 3: Kite connection...')
try:
    from kiteconnect import KiteConnect
    api_key = os.getenv('KITE_API_KEY')
    token   = os.getenv('KITE_ACCESS_TOKEN')
    kite    = KiteConnect(api_key=api_key)
    kite.set_access_token(token)
    profile = kite.profile()
    print('  User:', profile.get('user_name'))
    positions = kite.positions()
    print('  Open positions:', len(positions.get('net',[])))
    print('  PASS')
except Exception as e:
    print('  FAIL:', e)

print('Done.')
