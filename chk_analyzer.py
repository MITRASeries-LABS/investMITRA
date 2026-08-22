content = open('scripts/trade_analyzer.py', encoding='utf-8').read()
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'json.loads' in line or 'JSONDecodeError' in line or 'clean.split' in line:
        print(f'{i+1}: {line}')
