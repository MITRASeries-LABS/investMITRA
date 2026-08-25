content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Find the true gap calculation and add LTP fallback
old = '''            today_open = self.today_open.get(symbol)
            if not today_open:
                return'''

new = '''            today_open = self.today_open.get(symbol)
            if not today_open:
                # Fallback: use current LTP as proxy for open
                try:
                    q = self.kite.quote([f"NSE:{symbol}"])
                    today_open = float(q.get(f"NSE:{symbol}", {}).get("ohlc", {}).get("open", 0))
                    if today_open > 0:
                        self.today_open[symbol] = today_open
                        logger.debug("LTP open fallback: %s @ %.2f", symbol, today_open)
                    else:
                        return
                except:
                    return'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('LTP fallback added!')
else:
    print('Pattern not found - checking...')
    for i, line in enumerate(content.split('\n')):
        if 'today_open' in line and 'get(symbol)' in line:
            print(f'Line {i+1}: {repr(line)}')
