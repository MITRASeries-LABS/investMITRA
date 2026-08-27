import os, sys
sys.path.insert(0, 'scripts')
from dotenv import load_dotenv
load_dotenv('.env.prod')
from kiteconnect import KiteConnect
from intraday_signals import get_premarket_context, get_intraday_watchlist, GAP_THRESHOLDS, classify_gap
import psycopg2, json

kite = KiteConnect(api_key=os.getenv('KITE_API_KEY'))
kite.set_access_token(os.getenv('KITE_ACCESS_TOKEN'))

ctx = get_premarket_context()
long_list, short_list = get_intraday_watchlist(ctx)

long_syms  = {s['symbol']: s for s in long_list}
short_syms = {s['symbol']: s for s in short_list}
all_syms   = {**long_syms, **short_syms}

print(f'Long candidates: {len(long_list)}')
print(f'Short candidates: {len(short_list)}')
print()

# Check specific stocks that should have signaled
check = ['DLF','BEL','HAL','MCX','HINDCOPPER','HINDZINC','GLENMARK','PERSISTENT','COFORGE']
quotes = kite.quote([f"NSE:{s}" for s in check])

print(f'{"Symbol":<12} {"In List":>8} {"Score":>6} {"Cap":>6} {"Gap%":>7} {"GapType":<20} {"Reason"}')
print('-'*85)

conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()

for sym in check:
    q     = quotes.get(f"NSE:{sym}", {})
    ohlc  = q.get("ohlc", {})
    prev  = float(ohlc.get("close", 0))
    open_p= float(ohlc.get("open", 0))
    high  = float(ohlc.get("high", 0))
    low   = float(ohlc.get("low", 0))
    ltp   = float(q.get("last_price", 0))
    vol   = int(q.get("volume", 0))
    gap   = (open_p - prev) / prev * 100 if prev else 0

    in_list = 'LONG' if sym in long_syms else 'SHORT' if sym in short_syms else 'NOT IN LIST'
    stock   = all_syms.get(sym, {})
    score   = stock.get('investmitra_score', 0)
    cap     = stock.get('market_cap_category', '?')

    # Check gap threshold
    thresh = GAP_THRESHOLDS.get('momentum', 0.30)
    if cap in ('MICRO','SMALL'): thresh *= 0.7

    # Get avg volume for RVOL
    cur.execute("""
        SELECT AVG(volume) FROM investmitra.equity_prices_2026 ep
        JOIN investmitra.company_master cm ON ep.isin=cm.isin
        WHERE cm.nse_symbol=%s AND ep.trade_date >= CURRENT_DATE - 20
    """, (sym,))
    row = cur.fetchone()
    avg_vol = float(row[0]) if row and row[0] else 1
    rvol = vol / avg_vol if avg_vol > 0 else 0

    gap_type, _ = classify_gap(gap, vol, avg_vol)

    # Determine reason for no signal
    reasons = []
    if in_list == 'NOT IN LIST': reasons.append('not in watchlist')
    if abs(gap) < thresh: reasons.append(f'gap {gap:+.2f}% < thresh {thresh:.2f}%')
    if gap_type in ('fade_risk','exhaustion'): reasons.append(f'gap_type={gap_type}')
    if score < 55: reasons.append(f'score {score:.0f} < 55')
    if rvol < 1.5: reasons.append(f'rvol {rvol:.1f}x < 1.5')

    reason = ', '.join(reasons) if reasons else 'SHOULD HAVE SIGNALED!'
    print(f'{sym:<12} {in_list:>8} {score:>6.1f} {cap:>6} {gap:>+7.2f}% {gap_type:<20} {reason}')

cur.close()
conn.close()
