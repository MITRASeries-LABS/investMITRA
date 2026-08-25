content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'GAP_THRESHOLDS' in line:
        print(f'{i+1}: {repr(line)}')
