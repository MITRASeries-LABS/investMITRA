content = open('scripts/intraday_signals.py', encoding='utf-8').read()
old = "if sym in results_symbols: continue"
new = "if sym in ctx.get('results_today', set()): continue"
if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Fixed!')
else:
    print('Not found')
