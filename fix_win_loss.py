content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '''            wins = sum(1 for s in self.signals.values() 
                      if (s.get("exit_price",s["entry"])-s["entry"])*
                         (1 if s["direction"]=="LONG" else -1) > 0)'''

new = '''            wins = sum(1 for s in self.signals.values() 
                      if ((s.get("exit_price",s["entry"])-s["entry"])*
                         (1 if s["direction"]=="LONG" else -1) * 
                         s.get("position_size",1)) > 80)  # Must beat brokerage'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Win/loss fixed!')
else:
    print('Pattern not found')
