content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')

# Fix 1: Add traded_today to __init__ after gap_first_seen (line 804, index 803)
lines[803] = lines[803] + '\n        self.traded_today      = set()  # No re-entry same stock same day'
print('Fix 1: traded_today added to init')

content = '\n'.join(lines)

# Fix 2: Add to traded_today when signal fires (line 1201)
old2 = '        self.signals[symbol] = dict('
new2 = '        self.traded_today.add(symbol)  # Block re-entry today\n        self.signals[symbol] = dict('
content = content.replace(old2, new2)
print('Fix 2: traded_today updated on signal')

open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
print('Done!')
