content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '    if market_direction == "BULLISH":\n        short_list = []\n    elif market_direction == "BEARISH":\n        long_list  = []'

new = '''    if market_direction == "BULLISH":
        short_list = []
    elif market_direction == "BEARISH":
        # On bearish day - add quality long stocks as short candidates
        quality_shorts = [s for s in long_list if s.get('quality_score',0) >= 60]
        short_list = short_list + quality_shorts
        long_list  = []'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Bearish quality shorts fixed!')
else:
    print('Not found')
