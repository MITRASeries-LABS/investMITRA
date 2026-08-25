content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'short_list' in line.lower() and ('append' in line or 'direction' in line):
        print(f'{i+1}: {repr(line)}')
