import os, sys
sys.path.insert(0, 'scripts')
from dotenv import load_dotenv
load_dotenv('.env.prod')

from intraday_signals import classify_gap, GAP_THRESHOLDS
import psycopg2

conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()

print('Testing F&O SHORT signal logic...')
print()

test_cases = [
    ('HINDCOPPER', 'SMALL', 542.00, 511.00, 65, -5.60, 0.1),
    ('ATLANTAELE', 'MICRO', 1801.00, 1719.60, 63, -1.02, 0.1),
    ('HAL',        'LARGE', 4890.20, 4860.00, 67, -0.32, 0.1),
    ('BEL',        'LARGE', 407.00,  400.00,  63, -0.49, 0.1),
    ('MCX',        'MID',   3290.00, 3280.00, 77, +0.67, 0.1),
]

for sym, cap, today_open, ltp, score, gap_pct, ad_ratio in test_cases:
    # F&O check
    cur.execute("SELECT 1 FROM investmitra.fo_stocks WHERE symbol=%s", (sym,))
    is_fo = cur.fetchone() is not None

    abs_gap = abs(gap_pct)
    weak_market = ad_ratio < 0.3
    thresh = GAP_THRESHOLDS.get('momentum', 0.30)
    if cap in ('MICRO', 'SMALL'):
        thresh *= 0.7

    below_vwap = ltp < today_open
    above_vwap = ltp > today_open

    long_signal = (
        gap_pct > thresh and
        ltp >= today_open * 0.998 and
        above_vwap and score >= 60
    )

    short_signal = (
        weak_market and
        gap_pct < -thresh and
        ltp <= today_open * 1.002 and
        below_vwap and score >= 55 and
        is_fo
    )

    if long_signal:
        result = 'LONG SIGNAL'
    elif short_signal:
        result = 'SHORT SIGNAL'
    else:
        reason = 'not F&O' if not is_fo else 'gap/price check failed'
        result = f'no signal ({reason})'

    fo_str = 'F&O' if is_fo else 'non-F&O'
    print(f'{sym:<15} {cap:<6} {fo_str:<8} gap:{gap_pct:+.2f}% thresh:{thresh:.2f}% ? {result}')

cur.close()
conn.close()
