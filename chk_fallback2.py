content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')
for i in range(884, 902):
    print(f'{i+1}: {repr(lines[i])}')
