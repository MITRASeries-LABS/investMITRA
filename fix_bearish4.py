content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')

# Fix duplicate elif - remove line 1804 (index 1803)
# Lines: 1802=if BULLISH, 1803=short_list=[], 1804=elif BEARISH (dup), 1805=elif BEARISH, ...
del lines[1803]  # Remove the duplicate 'elif market_direction == "BEARISH":'

content = '\n'.join(lines)
open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
print('Duplicate removed!')
lines2 = content.split('\n')
for i in range(1800, 1815):
    print(f'{i+1}: {lines2[i]}')
