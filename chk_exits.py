content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')
print('=== GAP REVERSAL (990-1005) ===')
for i in range(989, 1005):
    print(f'{i+1}: {repr(lines[i])}')
