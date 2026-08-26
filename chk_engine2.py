content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')
# Show IntradayEngine init
for i, line in enumerate(lines):
    if 'def __init__' in line and i > 500:
        for j in range(i, i+15):
            print(f'{j+1}: {repr(lines[j])}')
        break
# Show engine creation
print('...')
for i in range(1499, 1510):
    print(f'{i+1}: {repr(lines[i])}')
