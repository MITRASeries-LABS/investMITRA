content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '        rvol = volume / avg_vol if avg_vol > 0 else 1'
new = '''        rvol = volume / avg_vol if avg_vol > 0 else 1
        rvol = min(rvol, 200.0)  # Cap RVOL at 200x ? anything higher is data error'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('RVOL sanity check added!')
else:
    print('Not found')
