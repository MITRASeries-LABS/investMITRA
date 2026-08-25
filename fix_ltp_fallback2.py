content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '''        today_open = self.today_open.get(symbol, 0)
        if today_open == 0: return'''

new = '''        today_open = self.today_open.get(symbol, 0)
        if today_open == 0:
            # Fallback: fetch open from Kite quote
            try:
                q = self.kite.quote([f"NSE:{symbol}"])
                today_open = float(q.get(f"NSE:{symbol}", {}).get("ohlc", {}).get("open", 0))
                if today_open > 0:
                    self.today_open[symbol] = today_open
                else:
                    return
            except:
                return'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('LTP fallback added!')
else:
    print('Not found')
    lines = content.split('\n')
    print(repr(lines[869]))
    print(repr(lines[870]))
