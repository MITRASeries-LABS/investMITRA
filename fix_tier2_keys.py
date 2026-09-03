content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Fix all missing keys in print statement
old = '''print(f"  {s['symbol']:<13} {s.get('cap', s.get('market_cap_category','?')):<7} {s['quality_score']:>5.1f} {s['investmitra_score']:>6.1f} {s['screen_count']:>4} {s['piotroski']:>3} {atr:>7.1f} {s['prev_day_chg']:>+5.1f}% {blk}")'''

new = '''print(f"  {s['symbol']:<13} {s.get('cap',s.get('market_cap_category','?')):<7} {s.get('quality_score',0):>5.1f} {s.get('investmitra_score',0):>6.1f} {s.get('screen_count',0):>4} {s.get('piotroski',s.get('piotroski_score',0)):>3} {atr:>7.1f} {s.get('prev_day_chg',0):>+5.1f}% {blk}")'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Fixed!')
else:
    # Find the line
    for i,line in enumerate(content.split('\n')):
        if 'piotroski' in line and 'print' in line and 'symbol' in line:
            print(f'Line {i+1}: {repr(line[:100])}')
