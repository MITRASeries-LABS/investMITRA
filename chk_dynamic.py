content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')
for i in range(1615, 1635):
    print(f'{i+1}: {repr(lines[i])}')
