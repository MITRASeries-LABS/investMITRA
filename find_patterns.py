content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'gap_first_seen' in line and 'self' in line:
        print(f'{i+1}: {repr(line[:80])}')
    if 'self.signals[symbol] =' in line:
        print(f'{i+1}: {repr(line[:80])}')
