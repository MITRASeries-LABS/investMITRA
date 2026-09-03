import sys
sys.path.insert(0, 'scripts')
from intraday_signals import classify_gap

tests = [
    (2.0, 500000, 100000, 'continuation_strong'),
    (0.5, 200000, 100000, 'continuation'),
    (0.2, 50000, 100000, 'fade_risk or small_gap'),
    (5.0, 100000, 100000, 'exhaustion'),
]

for gap, vol, avg, expected in tests:
    result, mult = classify_gap(gap, vol, avg)
    status = 'PASS' if expected in result or result in expected else 'FAIL'
    print(f'{status}: gap={gap:+.1f}% vol={vol/avg:.1f}x -> {result} (mult:{mult}) expected:{expected}')
