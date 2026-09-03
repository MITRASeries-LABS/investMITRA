content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')

# Replace lines 1804-1805
lines[1804] = '    elif market_direction == "BEARISH":'
lines[1805] = '''        # On bearish day add quality long stocks as short candidates
        quality_shorts = [dict(s, direction_override="SHORT") for s in long_list if s.get("quality_score",0) >= 60]
        short_list = quality_shorts + short_list
        long_list  = []'''

content = '\n'.join(lines)
open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
print('Fixed!')
lines2 = content.split('\n')
for i in range(1802, 1815):
    print(f'{i+1}: {lines2[i]}')
