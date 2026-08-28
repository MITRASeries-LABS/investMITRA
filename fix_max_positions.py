content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '''        can, reason = self.risk.can_trade()
        if not can: return'''

new = '''        # Paper trading - no position limit
        # can, reason = self.risk.can_trade()
        # if not can: return'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Position limit removed for paper trading!')
else:
    print('Not found')
    for i,line in enumerate(content.split('\n')):
        if 'can_trade' in line:
            print(f'Line {i+1}: {repr(line)}')
