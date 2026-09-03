content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')

# Find dead trade section
for i, line in enumerate(lines):
    if 'SMART EXIT' in line:
        print(f'Smart exit at line {i+1}')
        for j in range(i-2, i+15):
            print(f'{j+1}: {repr(lines[j])}')
        break
