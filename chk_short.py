content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')
print('=== Line 515-525 ===')
for i in range(514, 525):
    print(f'{i+1}: {repr(lines[i])}')
print('=== Line 1388-1398 ===')
for i in range(1387, 1398):
    print(f'{i+1}: {repr(lines[i])}')
