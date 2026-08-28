content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Add traded_today set to IntradayEngine __init__
old = '        self.gap_first_seen    = {}'
new = '''        self.gap_first_seen    = {}
        self.traded_today      = set()  # Never re-enter same stock same day'''

if old in content:
    content = content.replace(old, new)
    print('traded_today set added!')
else:
    print('init pattern not found')

# Block re-entry in _check_signal
old2 = '        if symbol in self.signals: return'
new2 = '''        if symbol in self.signals: return
        if symbol in self.traded_today: return  # No re-entry same day'''

if old2 in content:
    content = content.replace(old2, new2)
    print('Re-entry blocked!')
else:
    print('check_signal pattern not found')

# Add to traded_today when signal fires
old3 = '        self.signals[symbol] = sig'
new3 = '''        self.signals[symbol] = sig
        self.traded_today.add(symbol)  # Mark as traded today'''

if old3 in content:
    content = content.replace(old3, new3)
    print('traded_today updated on signal!')
else:
    print('signals pattern not found')

open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
