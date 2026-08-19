
import os, sys
sys.path.insert(0, 'scripts')
from dotenv import load_dotenv
load_dotenv('.env.prod')

print('Testing intraday components...')

# Test 1: Context + Watchlist
print('TEST 1: Watchlist...')
try:
    from intraday_signals import get_premarket_context, get_intraday_watchlist, get_rvol_baseline, get_key_levels, get_stock_sentiment, classify_gap
    ctx = get_premarket_context()
    print('  VIX:', ctx['india_vix'])
    print('  SGX:', ctx['sgx_change'])
    print('  Results today:', ctx['results_today'])
    long_list, short_list = get_intraday_watchlist(ctx)
    print('  LONG:', len(long_list), 'SHORT:', len(short_list))
    if long_list:
        s = long_list[0]
        print('  Top LONG:', s['symbol'], 'score:', s['investmitra_score'], 'quality:', s['quality_score'])
    print('  PASS')
except Exception as e:
    print('  FAIL:', e)

# Test 2: Key levels + ATR
print('TEST 2: Key levels + ATR...')
try:
    symbols = [s['symbol'] for s in long_list[:5]]
    kl = get_key_levels(symbols)
    for sym, data in kl.items():
        print('  ', sym, 'ATR:', round(data['atr14'],1), 'prev_high:', round(data['prev_high'],1), 'ma20:', round(data['ma20'],1))
    print('  PASS')
except Exception as e:
    print('  FAIL:', e)

# Test 3: RVOL
print('TEST 3: RVOL baseline...')
try:
    rvol = get_rvol_baseline()
    print('  Stocks with RVOL:', len(rvol))
    print('  PASS')
except Exception as e:
    print('  FAIL:', e)

# Test 4: Sentiment
print('TEST 4: Sentiment...')
try:
    symbols = [s['symbol'] for s in long_list[:10]]
    sent = get_stock_sentiment(symbols)
    print('  Stocks with sentiment:', len(sent))
    for sym, score in list(sent.items())[:3]:
        print('  ', sym, round(score,2))
    print('  PASS')
except Exception as e:
    print('  FAIL:', e)

# Test 5: Gap classification
print('TEST 5: Gap classification...')
try:
    tests = [(0.5,500000,300000),(1.8,800000,300000),(4.5,100000,300000),(-0.4,200000,300000)]
    for gap,vol,avg in tests:
        gtype, mult = classify_gap(gap, vol, avg)
        print('  gap:', gap, 'rvol:', round(vol/avg,1), '->', gtype, 'mult:', mult)
    print('  PASS')
except Exception as e:
    print('  FAIL:', e)

# Test 6: PnL table
print('TEST 6: intraday_pnl table...')
try:
    import psycopg2
    conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM investmitra.intraday_pnl')
    print('  Rows:', cur.fetchone()[0])
    conn.close()
    print('  PASS')
except Exception as e:
    print('  FAIL:', e)

print('Done.')
