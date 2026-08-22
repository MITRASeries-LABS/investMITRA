content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'get_market_direction' in line or 'market_direction' in line:
        print(f'{i+1}: {line[:80]}')
