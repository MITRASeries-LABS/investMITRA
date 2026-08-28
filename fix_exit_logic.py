content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'elapsed > DEAD_TRADE_MINUTES' in line:
        start = i
        break
print(f'Dead trade starts at line {start+1}')
for j in range(start, start+20):
    print(f'{j+1}: {lines[j]}')
