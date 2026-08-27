content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'market_cap_category' in line and ('WHERE' in line or 'AND' in line or 'IN' in line):
        print(f'{i+1}: {repr(line[:100])}')
