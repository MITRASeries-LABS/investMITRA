content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'rvol' in line.lower() and 'avg_vol' in line and '=' in line:
        print(f'{i+1}: {repr(line[:80])}')
