content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'self.kite' in line and 'def ' not in line:
        print(f'{i+1}: {repr(line[:60])}')
