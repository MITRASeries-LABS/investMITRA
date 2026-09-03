content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'BEARISH' in line and ('long_list' in line or 'short_list' in line):
        print(f'{i+1}: {repr(line)}')
