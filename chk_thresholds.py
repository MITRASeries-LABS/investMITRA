content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'ATR_TARGET' in line or 'ATR_STOP' in line or 'DEAD_TRADE' in line or 'inv >= 60' in line or 'score >= 60' in line or 'MIN_SCORE' in line:
        print(f'{i+1}: {repr(line[:100])}')
