content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'Dynamic scan:' in line and 'new gappers added' in line:
        print(f'{i+1}: {repr(line)}')
    if 'gappers added' in line:
        print(f'{i+1}: {repr(line)}')
