content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'IntradayEngine(' in line or 'self.kite =' in line:
        print(f'{i+1}: {repr(line[:80])}')
