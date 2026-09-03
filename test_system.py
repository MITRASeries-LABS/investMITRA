import os, sys
sys.path.insert(0, 'scripts')
from dotenv import load_dotenv
load_dotenv('.env.prod')

print('='*55)
print('investMITRA SYSTEM TEST')
print('='*55)

passed = 0
failed = 0

def test(name, fn):
    global passed, failed
    try:
        fn()
        print(f'  PASS: {name}')
        passed += 1
    except Exception as e:
        print(f'  FAIL: {name} ? {e}')
        failed += 1

# Test 1: Import
def t1():
    from intraday_signals import (
        get_premarket_context, get_intraday_watchlist,
        get_rvol_baseline, get_key_levels, classify_gap,
        GAP_THRESHOLDS, load_signal_weights, load_signal_thresholds,
        get_dynamic_gappers, get_nse_gainers_losers
    )
test('Import all functions', t1)

# Test 2: Gap classification
def t2():
    from intraday_signals import classify_gap
    assert classify_gap(2.0, 500000, 100000)[0] in ('continuation_strong','continuation')
    assert classify_gap(0.5, 200000, 100000)[0] == 'continuation'
    assert classify_gap(0.2, 50000, 100000)[0] in ('fade_risk','small_gap')
    assert classify_gap(5.0, 100000, 100000)[0] == 'exhaustion'
test('Gap classification', t2)

# Test 3: Signal weights load
def t3():
    from intraday_signals import load_signal_weights
    w = load_signal_weights()
    assert isinstance(w, dict)
    assert 'gap_score' in w or len(w) > 0
test('Signal weights load', t3)

# Test 4: Thresholds load
def t4():
    from intraday_signals import load_signal_thresholds
    t = load_signal_thresholds()
    assert t['tier1_score_min'] >= 40
    assert t['tier2_gap_min'] >= 0.5
test('Signal thresholds load', t4)

# Test 5: Premarket context
def t5():
    from intraday_signals import get_premarket_context
    ctx = get_premarket_context()
    assert 'india_vix' in ctx
    assert 'vix_signal' in ctx
    assert 'results_today' in ctx
test('Premarket context', t5)

# Test 6: Watchlist
def t6():
    from intraday_signals import get_premarket_context, get_intraday_watchlist
    ctx = get_premarket_context()
    long_list, short_list = get_intraday_watchlist(ctx)
    assert len(long_list) >= 10, f'Only {len(long_list)} stocks'
    assert all('symbol' in s for s in long_list)
    assert all('investmitra_score' in s for s in long_list)
test('Watchlist loads 10+ stocks', t6)

# Test 7: RVOL baseline
def t7():
    from intraday_signals import get_rvol_baseline
    rvol = get_rvol_baseline()
    assert len(rvol) > 100
test('RVOL baseline 100+ stocks', t7)

# Test 8: NSE gainers
def t8():
    from intraday_signals import get_nse_gainers_losers
    gainers, losers = get_nse_gainers_losers()
    assert len(gainers) > 0 or len(losers) > 0
test('NSE gainers/losers', t8)

# Test 9: Neon tables exist
def t9():
    import psycopg2
    conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
    cur = conn.cursor()
    tables = ['trade_log','trade_insights','signal_weights',
              'intraday_pnl','signal_thresholds','fo_stocks']
    for t in tables:
        cur.execute(f"SELECT COUNT(*) FROM investmitra.{t}")
    conn.close()
test('All Neon tables exist', t9)

# Test 10: Kite connection
def t10():
    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=os.getenv('KITE_API_KEY'))
    kite.set_access_token(os.getenv('KITE_ACCESS_TOKEN'))
    profile = kite.profile()
    assert profile.get('user_name')
test('Kite connection', t10)

print()
print(f'Results: {passed} passed, {failed} failed')
print('='*55)
if failed == 0:
    print('ALL TESTS PASSED - Safe to trade')
else:
    print('FIX FAILURES BEFORE TRADING')
print('='*55)
