content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '''            print(f"\\n  ?? Dynamic scan: {len(new_gappers)} new gappers added")
            for g in new_gappers:
                print(f"     {g['symbol']}: gap {g.get('gap_pct',0):+.2f}%")'''

new = '''            # Pre-populate opens for dynamic stocks immediately
            dynamic_syms = [g['symbol'] for g in new_gappers]
            try:
                dyn_quotes = kite.quote([f"NSE:{s}" for s in dynamic_syms])
                for g in new_gappers:
                    sym = g['symbol']
                    q   = dyn_quotes.get(f"NSE:{sym}", {})
                    open_p = float(q.get("ohlc", {}).get("open", 0))
                    prev_c = float(q.get("ohlc", {}).get("close", 0))
                    if open_p > 0:
                        engine.today_open[sym]  = open_p
                        engine.prev_close[sym]  = prev_c
                        logger.info("Pre-populated open: %s @ %.2f", sym, open_p)
            except Exception as e:
                logger.warning("Pre-populate opens failed: %s", e)

            print(f"\\n  ?? Dynamic scan: {len(new_gappers)} new gappers added")
            for g in new_gappers:
                print(f"     {g['symbol']}: gap {g.get('gap_pct',0):+.2f}%")'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Opens pre-populated for dynamic stocks!')
else:
    print('Pattern not found')
    idx = content.find('Dynamic scan:')
    print(repr(content[idx-50:idx+200]))
