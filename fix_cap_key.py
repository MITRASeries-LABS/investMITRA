content = open('scripts/intraday_signals.py', encoding='utf-8').read()
old = "s['cap']"
new = "s.get('cap', s.get('market_cap_category','?'))"
content = content.replace(old, new)
open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
print('Fixed!')
