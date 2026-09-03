content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')

# Replace lines 992-1001 with smarter gap reversal
new_code = [
    '        # Gap reversal exit: only if BELOW ENTRY for extended time',
    '        # Not just below open - stock must be losing money',
    '        today_open = self.today_open.get(symbol, entry)',
    '        if is_long and ltp < today_open * 0.995:  # 0.5% below open',
    '            # Only exit if ALSO below entry (actually losing)',
    '            if ltp < entry * 0.998:',
    '                # Track how long below open',
    '                below_open_key = f"{symbol}_below_open"',
    '                if below_open_key not in self.gap_first_seen:',
    '                    self.gap_first_seen[below_open_key] = now',
    '                else:',
    '                    mins_below = (now - self.gap_first_seen[below_open_key]).total_seconds() / 60',
    '                    if mins_below >= 10:  # Below open for 10+ minutes',
    '                        del self.gap_first_seen[below_open_key]',
    '                        pnl = self.risk.close_position(symbol, ltp)',
    '                        print(f"\\n  ?? GAP REVERSAL EXIT: {symbol} @ \\u20b9{ltp:.2f} | Below entry {mins_below:.0f}min | Net: \\u20b9{pnl:.0f}\\n")',
    '                        try:',
    '                            from order_manager import notify as tg_notify',
    '                            tg_notify(f"GAP REVERSAL EXIT - {symbol}\\nBelow entry for {mins_below:.0f}min\\nExit: {ltp:.2f}\\nNet: {pnl:.0f}\\nDaily P&L: {self.risk.net_pnl:.0f}")',
    '                        except: pass',
    '                        return',
    '            else:',
    '                # Above entry but below open - remove below_open timer',
    '                self.gap_first_seen.pop(f"{symbol}_below_open", None)',
    '        else:',
    '            # Above open - clear any below_open timer',
    '            self.gap_first_seen.pop(f"{symbol}_below_open", None)',
]

lines[991:1001] = new_code
content = '\n'.join(lines)
open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
print('Smart gap reversal exit applied!')

# Verify
lines2 = content.split('\n')
for i in range(991, 1018):
    print(f'{i+1}: {lines2[i]}')
