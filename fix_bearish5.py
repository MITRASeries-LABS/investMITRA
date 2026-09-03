content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')

# Remove lines 1808-1809 (the misplaced weak_market log from old elif)
del lines[1808:1810]

content = '\n'.join(lines)
open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
print('Fixed!')
lines2 = content.split('\n')
for i in range(1800, 1815):
    print(f'{i+1}: {lines2[i]}')
