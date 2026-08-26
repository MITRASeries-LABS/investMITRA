content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'class IntradayEngine' in line:
        for j in range(i, i+25):
            print(f'{j+1}: {repr(lines[j])}')
        break
