content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'get_intraday_watchlist' in line and 'def' in line:
        for j in range(i, i+60):
            print(f'{j+1}: {lines[j]}')
        break
