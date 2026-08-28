content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')

# Insert pre-populate opens before line 1667 (index 1666)
new_code = [
    '            # Pre-populate opens for dynamic stocks immediately',
    '            dynamic_syms = [g["symbol"] for g in new_gappers]',
    '            try:',
    '                dyn_quotes = kite.quote([f"NSE:{s}" for s in dynamic_syms])',
    '                for g in new_gappers:',
    '                    sym = g["symbol"]',
    '                    q   = dyn_quotes.get(f"NSE:{sym}", {})',
    '                    open_p = float(q.get("ohlc", {}).get("open", 0))',
    '                    prev_c = float(q.get("ohlc", {}).get("close", 0))',
    '                    if open_p > 0:',
    '                        engine.today_open[sym] = open_p',
    '                        engine.prev_close[sym] = prev_c',
    '                        logger.info("Pre-populated open: %s @ %.2f", sym, open_p)',
    '            except Exception as e:',
    '                logger.warning("Pre-populate opens: %s", e)',
]

lines[1666:1666] = new_code
content = '\n'.join(lines)
open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
print('Fixed! Verifying...')
lines2 = content.split('\n')
for i in range(1664, 1685):
    print(f'{i+1}: {lines2[i]}')
