content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')

new_code = [
    '        # Dead trade exit: no movement after time limit',
    '        elapsed = (now - pos["signal_time"]).total_seconds() / 60',
    '        today_open = self.today_open.get(symbol, entry)',
    '        gap_intact = (is_long and ltp >= today_open) or (not is_long and ltp <= today_open)',
    '        above_entry = (is_long and ltp >= entry) or (not is_long and ltp <= entry)',
    '',
    '        if elapsed > DEAD_TRADE_MINUTES and not pos["partial_done"]:',
    '            if above_entry and gap_intact:',
    '                # Profitable or flat + gap intact = hold until 3PM',
    '                logger.debug("Holding: %s ltp=%.2f entry=%.2f gap intact", symbol, ltp, entry)',
    '            elif above_entry and not gap_intact and elapsed < DEAD_TRADE_MINUTES * 3:',
    '                # Above entry but gap filled = give more time',
    '                logger.debug("Above entry, gap filled: %s - extending", symbol)',
    '            else:',
    '                # Below entry OR time limit exceeded = exit',
    '                pnl = self.risk.close_position(symbol, ltp)',
    '                reason = "below entry" if not above_entry else "time limit"',
    '                print(f"\\n  \\u23f0 DEAD TRADE EXIT: {symbol} @ \\u20b9{ltp:.2f} | {reason} after {elapsed:.0f}min | Net: \\u20b9{pnl:.0f}\\n")',
    '                try:',
    '                    from order_manager import notify as tg_notify',
    '                    tg_notify(f"DEAD TRADE EXIT - {symbol}\\n{reason} after {elapsed:.0f}min\\nExit: {ltp:.2f}\\nNet: {pnl:.0f}\\nDaily P&L: {self.risk.net_pnl:.0f}")',
    '                except: pass',
    '                return',
]

# Find and replace lines 1020-1050
start = 1019  # 0-indexed
end = start
for i in range(start, min(start+40, len(lines))):
    if i > start and ('# Partial exit' in lines[i] or '# partial exit' in lines[i].lower()):
        end = i
        break

print(f'Replacing lines {start+1} to {end+1}')
lines[start:end] = new_code
content = '\n'.join(lines)
open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
print('Dead trade v3 applied!')
