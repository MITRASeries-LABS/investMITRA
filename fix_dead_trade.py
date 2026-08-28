content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '''            if elapsed > DEAD_TRADE_MINUTES and not pos["partial_done"]:
                ltp = self.vwap.get(symbol, pos["entry"])  # use last known price
                pnl = (ltp - pos["entry"]) * pos["size"] if pos["direction"]=="LONG" else (pos["entry"] - ltp) * pos["size"]
                pnl -= BROKERAGE_PER_TRADE
                self.risk.daily_pnl      += pnl
                self.risk.daily_brokerage += BROKERAGE_PER_TRADE
                self.risk.trades_today   += 1
                del self.signals[symbol]
                del self.risk.positions[symbol]
                print(f"\\n  ? DEAD TRADE EXIT: {symbol} @ ?{ltp:.2f} | No move in {DEAD_TRADE_MINUTES}min | Net: ?{pnl:.0f}\\n")'''

new = '''            if elapsed > DEAD_TRADE_MINUTES and not pos["partial_done"]:
                ltp   = self.vwap.get(symbol, pos["entry"])
                entry = pos["entry"]
                today_open = self.today_open.get(symbol, entry)
                vwap  = self.vwap.get(symbol, ltp)

                # Don't exit if stock is still trending above VWAP and open
                if pos["direction"] == "LONG":
                    still_trending = ltp > today_open and ltp > vwap * 0.998
                else:
                    still_trending = ltp < today_open and ltp < vwap * 1.002

                if still_trending and elapsed < DEAD_TRADE_MINUTES * 2:
                    # Give trending stock more time (2x dead trade window)
                    logger.debug("Dead trade extended: %s still trending ltp=%.2f vwap=%.2f", symbol, ltp, vwap)
                    continue

                pnl = (ltp - entry) * pos["size"] if pos["direction"]=="LONG" else (entry - ltp) * pos["size"]
                pnl -= BROKERAGE_PER_TRADE
                self.risk.daily_pnl      += pnl
                self.risk.daily_brokerage += BROKERAGE_PER_TRADE
                self.risk.trades_today   += 1
                del self.signals[symbol]
                del self.risk.positions[symbol]
                print(f"\\n  ? DEAD TRADE EXIT: {symbol} @ ?{ltp:.2f} | No move in {DEAD_TRADE_MINUTES}min | Net: ?{pnl:.0f}\\n")'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Dead trade fix applied - trending stocks get more time!')
else:
    print('Pattern not found')
    for i,line in enumerate(content.split('\n')):
        if 'DEAD_TRADE_MINUTES' in line and 'elapsed' in line:
            print(f'Line {i+1}: {repr(line[:80])}')
