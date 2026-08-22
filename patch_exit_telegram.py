content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Fix 1: Stoploss exit
old1 = '            print(f"\\n  🛑 STOPLOSS: {symbol} @ ₹{ltp:.2f} | Net: ₹{pnl:.0f}\\n")'
new1 = '''            print(f"\\n  🛑 STOPLOSS: {symbol} @ ₹{ltp:.2f} | Net: ₹{pnl:.0f}\\n")
            try:
                from order_manager import notify as tg_notify
                tg_notify(f"STOPLOSS - {symbol}\\nExit: {ltp:.2f}\\nNet: {pnl:.0f}\\nDaily P&L: {self.risk.net_pnl:.0f}")
            except: pass'''

# Fix 2: Gap reversal exit
old2 = '            print(f"\\n  🔄 GAP REVERSAL EXIT: {symbol} @ ₹{ltp:.2f} | Gap filled | Net: ₹{pnl:.0f}\\n")'
new2 = '''            print(f"\\n  🔄 GAP REVERSAL EXIT: {symbol} @ ₹{ltp:.2f} | Gap filled | Net: ₹{pnl:.0f}\\n")
            try:
                from order_manager import notify as tg_notify
                tg_notify(f"GAP REVERSAL EXIT - {symbol}\\nExit: {ltp:.2f}\\nNet: {pnl:.0f}\\nDaily P&L: {self.risk.net_pnl:.0f}")
            except: pass'''

# Fix 3: Dead trade exit
old3 = '            print(f"\\n  ⏰ DEAD TRADE EXIT: {symbol} @ ₹{ltp:.2f} | No move in {DEAD_TRADE_MINUTES}min | Net: ₹{pnl:.0f}\\n")'
new3 = '''            print(f"\\n  ⏰ DEAD TRADE EXIT: {symbol} @ ₹{ltp:.2f} | No move in {DEAD_TRADE_MINUTES}min | Net: ₹{pnl:.0f}\\n")
            try:
                from order_manager import notify as tg_notify
                tg_notify(f"DEAD TRADE EXIT - {symbol}\\nNo move in {DEAD_TRADE_MINUTES}min\\nExit: {ltp:.2f}\\nNet: {pnl:.0f}\\nDaily P&L: {self.risk.net_pnl:.0f}")
            except: pass'''

# Fix 4: Partial exit
old4 = '            net = pnl - BROKERAGE_PER_TRADE // 2\n            print(f"\\n  💰 PARTIAL EXIT: {symbol} — {partial_size} sh @ ₹{ltp:.2f} | Net: +₹{net:.0f}")'
new4 = '''            net = pnl - BROKERAGE_PER_TRADE // 2
            print(f"\\n  💰 PARTIAL EXIT: {symbol} — {partial_size} sh @ ₹{ltp:.2f} | Net: +₹{net:.0f}")
            try:
                from order_manager import notify as tg_notify
                tg_notify(f"PARTIAL EXIT - {symbol}\\n{partial_size} shares @ {ltp:.2f}\\nNet: +{net:.0f}\\nStop -> breakeven\\nDaily P&L: {self.risk.net_pnl:.0f}")
            except: pass'''

# Fix 5: Session exit (3PM)
old5 = '            if pnl is not None:\n                print(f"\\n  🏁 SESSION EXIT: {symbol} @ ₹{ltp:.2f} | Net: ₹{pnl:.0f}\\n")'
new5 = '''            if pnl is not None:
                print(f"\\n  🏁 SESSION EXIT: {symbol} @ ₹{ltp:.2f} | Net: ₹{pnl:.0f}\\n")
                try:
                    from order_manager import notify as tg_notify
                    tg_notify(f"3PM EXIT - {symbol}\\nExit: {ltp:.2f}\\nNet: {pnl:.0f}\\nDaily P&L: {self.risk.net_pnl:.0f}")
                except: pass'''

fixes = [
    (old1, new1, "Stoploss"),
    (old2, new2, "Gap reversal"),
    (old3, new3, "Dead trade"),
    (old4, new4, "Partial exit"),
    (old5, new5, "Session exit"),
]

fixed = 0
for old, new, name in fixes:
    if old in content:
        content = content.replace(old, new)
        print(f"Fixed: {name}")
        fixed += 1
    else:
        print(f"Not found: {name}")

if fixed > 0:
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print(f"\nTotal fixed: {fixed}/5")
else:
    print("Nothing fixed")
