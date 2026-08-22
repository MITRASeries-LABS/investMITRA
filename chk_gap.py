content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')
for i in range(494, 515):
    print(f'{i+1}: {lines[i]}')
