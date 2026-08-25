content = open('scripts/weight_optimizer.py', encoding='utf-8').read()

old = 'IMPORTANT: weights must sum to exactly 1.0'

new = '''IMPORTANT: weights must sum to exactly 1.0

HARD CONSTRAINTS (never change these):
1. Must generate signals from ALL cap categories: MICRO, SMALL, MID, LARGE
   Do not raise gap thresholds so high that MICRO/SMALL stocks are excluded
   MICRO/SMALL threshold should always be 70% of MID/LARGE threshold
2. Minimum 2-4 signals per day expected ? if win rate is low, fix quality filters
   not by raising thresholds to zero signals
3. gap_threshold_momentum must never exceed 0.40% ? beyond that no signals fire
4. Always keep rvol_min_continuation below 2.5x ? higher kills all signals'''

if old in content:
    content = content.replace(old, new)
    open('scripts/weight_optimizer.py', 'w', encoding='utf-8').write(content)
    print('Opus prompt updated with hard constraints!')
else:
    print('Pattern not found')
