content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')

# Show both filter blocks
print('Block 1 (lines 28-42):')
for i in range(27, 42):
    print(f'{i+1}: {repr(lines[i])}')
print()
print('Block 2 (lines 41-52):')
for i in range(40, 53):
    print(f'{i+1}: {repr(lines[i])}')
