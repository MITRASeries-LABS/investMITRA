content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')

new_dead_trade = [
    '        # SMART EXIT: Exit based on price action, not just time',
    '        today_open = self.today_open.get(symbol, entry)',
    '        vwap = self.vwap.get(symbol, ltp)',
    '        gap_intact = (is_long and ltp > today_open) or (not is_long and ltp < today_open)',
    '        above_vwap = (is_long and ltp >= vwap * 0.998) or (not is_long and ltp <= vwap * 1.002)',
    '',
    '        if elapsed > DEAD_TRADE_MINUTES and not pos["partial_done"]:',
    '            if gap_intact and elapsed < DEAD_TRADE_MINUTES * 3:',
    '                # Gap still intact - give more time (up to 3x)',
    '                logger.debug("Gap intact: %s ltp=%.2f open=%.2f - holding", symbol, ltp, today_open)',
    '            elif not gap_intact and elapsed > 20:',
    '                # Gap filled - thesis broken - exit immediately',
    '                pnl = self.risk.close_position(symbol, ltp)',
    '                print(f"\\n  ? GAP FILLED EXIT: {symbol} @ \\u20b9{ltp:.2f} | Gap filled after {elapsed:.0f}min | Net: \\u20b9{pnl:.0f}\\n")',
    '                try:',
    '                    from order_manager import notify as tg_notify',
    '                    tg_notify(f"GAP FILLED EXIT - {symbol}\\nGap filled after {elapsed:.0f}min\\nExit: {ltp:.2f}\\nNet: {pnl:.0f}\\nDaily P&L: {self.risk.net_pnl:.0f}")',
    '                except: pass',
    '                return',
    '            else:',
    '                # Time limit reached and gap borderline - exit',
    '                pnl = self.risk.close_position(symbol, ltp)',
    '                print(f"\\n  ? DEAD TRADE EXIT: {symbol} @ \\u20b9{ltp:.2f} | No move in {elapsed:.0f}min | Net: \\u20b9{pnl:.0f}\\n")',
    '                try:',
    '                    from order_manager import notify as tg_notify',
    '                    tg_notify(f"DEAD TRADE EXIT - {symbol}\\nNo move in {elapsed:.0f}min\\nExit: {ltp:.2f}\\nNet: {pnl:.0f}\\nDaily P&L: {self.risk.net_pnl:.0f}")',
    '                except: pass',
    '                return',
]

# Replace lines 880-899 (index 880-899)
lines[880:900] = new_dead_trade
content = '\n'.join(lines)
open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
print('Smart exit logic applied!')
print()
# Verify
lines2 = content.split('\n')
for i in range(879, 905):
    print(f'{i+1}: {lines2[i]}')
