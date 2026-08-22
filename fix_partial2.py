content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '                print(f"\\n  PARTIAL EXIT: {symbol} - {partial_size} sh @ {ltp:.2f} | Net: +{net:.0f}")'
new = '''                print(f"\\n  PARTIAL EXIT: {symbol} - {partial_size} sh @ {ltp:.2f} | Net: +{net:.0f}")
                try:
                    from order_manager import notify as tg_notify
                    tg_notify(f"PARTIAL EXIT - {symbol}\\n{partial_size} shares @ {ltp:.2f}\\nNet: +{net:.0f}\\nStop -> breakeven\\nDaily P&L: {self.risk.net_pnl:.0f}")
                except: pass'''

lines = content.split('\n')
for i, line in enumerate(lines):
    if 'PARTIAL EXIT' in line and 'print' in line and 'sh @' in line:
        lines[i] = line + '\n                try:\n                    from order_manager import notify as tg_notify\n                    tg_notify(f"PARTIAL EXIT - {symbol}\\\\n{partial_size} shares @ {ltp:.2f}\\\\nNet: +{net:.0f}\\\\nStop -> breakeven\\\\nDaily P&L: {self.risk.net_pnl:.0f}")\n                except: pass'
        print(f"Fixed line {i+1}")
        break

content = '\n'.join(lines)
open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
print('Done')
