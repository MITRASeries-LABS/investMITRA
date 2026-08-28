content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')

# Replace lines 880-887 with trending check
new_code = [
    '        if elapsed > DEAD_TRADE_MINUTES and not pos["partial_done"]:',
    '            # Check if stock still trending before exiting',
    '            today_open = self.today_open.get(symbol, entry)',
    '            vwap = self.vwap.get(symbol, ltp)',
    '            if is_long:',
    '                still_trending = ltp > today_open and ltp >= vwap * 0.998',
    '            else:',
    '                still_trending = ltp < today_open and ltp <= vwap * 1.002',
    '            # Give trending stock 2x time before exiting',
    '            if still_trending and elapsed < DEAD_TRADE_MINUTES * 2:',
    '                logger.debug("Extended: %s trending ltp=%.2f open=%.2f", symbol, ltp, today_open)',
    '            else:',
    '                pnl = self.risk.close_position(symbol, ltp)',
    '                print(f"\\n  ? DEAD TRADE EXIT: {symbol} @ ?{ltp:.2f} | No move in {DEAD_TRADE_MINUTES}min | Net: ?{pnl:.0f}\\n")',
    '                try:',
    '                    from order_manager import notify as tg_notify',
    '                    tg_notify(f"DEAD TRADE EXIT - {symbol}\\nNo move in {DEAD_TRADE_MINUTES}min\\nExit: {ltp:.2f}\\nNet: {pnl:.0f}\\nDaily P&L: {self.risk.net_pnl:.0f}")',
    '                except: pass',
    '                return',
]

lines[879:887] = new_code
content = '\n'.join(lines)
open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
print('Dead trade trend check applied!')
# Verify
lines2 = content.split('\n')
for i in range(879, 900):
    print(f'{i+1}: {lines2[i]}')
