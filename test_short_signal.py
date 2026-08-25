import os, sys
sys.path.insert(0, 'scripts')
from dotenv import load_dotenv
load_dotenv('.env.prod')

from intraday_signals import classify_gap, GAP_THRESHOLDS

print('Testing SHORT signal logic with yesterday data...')
print()

# Simulate yesterday's stocks
test_cases = [
    ('HINDCOPPER', 'SMALL', 542.00, 511.00, 65, -5.60, 0.1),  # gap -5.6%, breadth 0.1x
    ('ATLANTAELE', 'MICRO', 1801.00, 1719.60, 63, -1.02, 0.1),
    ('HAL',        'LARGE', 4890.20, 4900.00, 67, -0.32, 0.1),
    ('BEL',        'LARGE', 407.00, 413.25, 63, -0.49, 0.1),
    ('MCX',        'MID',   3290.00, 3280.00, 77, +0.67, 0.1),  # gapped UP
]

for sym, cap, today_open, ltp, score, gap_pct, ad_ratio in test_cases:
    abs_gap = abs(gap_pct)
    weak_market = ad_ratio < 0.3

    # Gap threshold (MICRO/SMALL = 0.7x)
    thresh = GAP_THRESHOLDS.get('momentum', 0.30)
    if cap in ('MICRO', 'SMALL'):
        thresh *= 0.7

    below_vwap = ltp < today_open  # simplified
    above_vwap = ltp > today_open

    # LONG check
    long_signal = (
        gap_pct > thresh and
        ltp >= today_open * 0.998 and
        above_vwap and score >= 60
    )

    # SHORT Option 2 (quality stock, weak market)
    short_signal = (
        weak_market and
        gap_pct < -thresh and
        ltp <= today_open * 1.002 and
        below_vwap and score >= 55
    )

    if long_signal:
        result = 'LONG SIGNAL'
    elif short_signal:
        result = 'SHORT SIGNAL'
    else:
        result = 'no signal'

    print(f'{sym:<15} {cap:<6} gap:{gap_pct:+.2f}% thresh:{thresh:.2f}% score:{score} ? {result}')
