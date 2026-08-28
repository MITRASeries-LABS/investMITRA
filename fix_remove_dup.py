content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')

# Remove lines 41-50 (index 40-49) - the duplicate filter
del lines[40:50]

content = '\n'.join(lines)
open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
print('Duplicate filter removed!')

# Verify
lines2 = content.split('\n')
print('Lines 28-45 now:')
for i in range(27, 45):
    print(f'{i+1}: {lines2[i]}')
