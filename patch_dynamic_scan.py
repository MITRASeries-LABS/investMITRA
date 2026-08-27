content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Add dynamic gap scanner function after get_intraday_watchlist
old = 'def get_rvol_baseline() -> dict:'

new = '''def get_dynamic_gappers(kite, existing_symbols: set, ctx: dict) -> list[dict]:
    """
    Dynamic gap scanner — runs at 9:30 AM after opens captured.
    Scans top 200 NSE stocks by traded value for genuine gaps.
    Adds any gapping stock not already in watchlist.
    """
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=15)
        cur  = conn.cursor()

        # Get top 200 stocks by traded value — full NSE universe
        cur.execute("""
            SELECT cm.nse_symbol, cm.market_cap_category,
                   ds.investmitra_score, ds.sector,
                   ep.close AS prev_close,
                   AVG(ep.volume) OVER (PARTITION BY ep.isin) AS avg_vol,
                   AVG(ep.close * ep.volume) OVER (PARTITION BY ep.isin) AS avg_traded
            FROM investmitra.equity_prices ep
            JOIN investmitra.company_master cm ON ep.isin = cm.isin
            LEFT JOIN investmitra.daily_scores ds ON ep.isin = ds.isin
                AND ds.score_date = (SELECT MAX(score_date) FROM investmitra.daily_scores)
            WHERE ep.trade_date = (SELECT MAX(trade_date) FROM investmitra.equity_prices)
              AND cm.nse_symbol IS NOT NULL
              AND ep.close BETWEEN 50 AND 20000
              AND ep.close * ep.volume >= 2000000
            ORDER BY ep.close * ep.volume DESC
            LIMIT 200
        """)
        rows = cur.fetchall()
        cur.close(); conn.close()

        # Filter out already in watchlist
        candidates = []
        for row in rows:
            sym = row[0]
            if sym in existing_symbols: continue
            if not sym: continue
            candidates.append({
                'symbol':               sym,
                'market_cap_category':  row[1] or 'MID',
                'investmitra_score':    float(row[2] or 50),
                'sector':               row[3] or '',
                'prev_close':           float(row[4] or 0),
                'avg_vol':              float(row[5] or 0),
                'avg_traded':           float(row[6] or 0),
                'quality_score':        float(row[2] or 50),
                'screen_count':         0,
                'piotroski_score':      0,
                'bulk_deal':            False,
                'prev_day_chg':         0,
                'company_name':         sym,
            })

        if not candidates:
            return []

        # Fetch live quotes for all candidates
        syms = [c['symbol'] for c in candidates]
        # Batch into groups of 50 (Kite limit)
        dynamic_gappers = []
        results_today = ctx.get('results_today', set())

        for i in range(0, len(syms), 50):
            batch = syms[i:i+50]
            try:
                quotes = kite.quote([f"NSE:{s}" for s in batch])
            except:
                continue

            for c in candidates[i:i+50]:
                sym   = c['symbol']
                if sym in results_today: continue

                q      = quotes.get(f"NSE:{sym}", {})
                ohlc   = q.get("ohlc", {})
                open_p = float(ohlc.get("open", 0))
                prev   = float(ohlc.get("close", 0)) or c['prev_close']
                ltp    = float(q.get("last_price", 0))
                vol    = int(q.get("volume", 0))

                if not open_p or not prev or not ltp: continue

                gap_pct = (open_p - prev) / prev * 100
                avg_vol = c['avg_vol'] or 100000

                # Early morning RVOL adjustment
                from datetime import datetime, timezone, timedelta
                _ist = timezone(timedelta(hours=5, minutes=30))
                _early = datetime.now(_ist).hour < 10
                if _early: avg_vol *= 0.4

                gap_type, _ = classify_gap(gap_pct, vol, avg_vol)

                # Only genuine continuation gaps
                cap    = c['market_cap_category']
                thresh = GAP_THRESHOLDS.get('momentum', 0.30)
                if cap in ('MICRO','SMALL'): thresh *= 0.7

                if (abs(gap_pct) >= thresh and
                    gap_type not in ('exhaustion', 'fade_risk', 'small_gap')):
                    c['gap_pct']  = gap_pct
                    c['gap_type'] = gap_type
                    c['ltp']      = ltp
                    dynamic_gappers.append(c)
                    logger.info("Dynamic gapper: %s gap=%.2f%% (%s)", sym, gap_pct, gap_type)

        logger.info("Dynamic scan: %d new gappers from top-200 universe", len(dynamic_gappers))
        return dynamic_gappers[:20]  # Max 20 additional stocks

    except Exception as e:
        logger.warning("Dynamic gap scan failed: %s", e)
        return []


def get_rvol_baseline() -> dict:'''

if old in content:
    content = content.replace(old, new)
    print('Dynamic gap scanner added!')
else:
    print('Pattern not found')

# Now wire it into main() — call after opens captured at 9:30 AM
old2 = '''    def on_connect(ws, response):
        logger.info("Connected — %d tokens", len(tokens))
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)'''

new2 = '''    # Dynamic gap scan at 9:32 AM — find additional gappers
    import threading
    def _dynamic_scan():
        import time as _time
        from datetime import datetime, timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        # Wait until 9:32 AM
        while True:
            now = datetime.now(ist)
            if now.hour == 9 and now.minute >= 32:
                break
            if now.hour > 9:
                break
            _time.sleep(10)

        existing = set(engine.all_stocks.keys())
        new_gappers = get_dynamic_gappers(kite, existing, ctx)

        if new_gappers:
            # Add to engine watchlist
            for g in new_gappers:
                engine.long_map[g['symbol']] = g
                engine.all_stocks[g['symbol']] = g

            # Subscribe new tokens
            new_tokens = []
            for g in new_gappers:
                try:
                    instr = kite.ltp([f"NSE:{g['symbol']}"])
                    for k,v in instr.items():
                        tok = v.get('instrument_token')
                        if tok:
                            engine.token_map[g['symbol']] = tok
                            engine.rev_tokens[tok] = g['symbol']
                            new_tokens.append(tok)
                except: pass

            if new_tokens:
                logger.info("Subscribing %d dynamic gapper tokens", len(new_tokens))
                # Will subscribe on next reconnect or via ticker
                try:
                    ticker.subscribe(new_tokens)
                    ticker.set_mode(ticker.MODE_FULL, new_tokens)
                except: pass

            print(f"\\n  🔍 Dynamic scan: {len(new_gappers)} new gappers added")
            for g in new_gappers:
                print(f"     {g['symbol']}: gap {g.get('gap_pct',0):+.2f}%")

    threading.Thread(target=_dynamic_scan, daemon=True).start()

    def on_connect(ws, response):
        logger.info("Connected — %d tokens", len(tokens))
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)'''

if old2 in content:
    content = content.replace(old2, new2)
    print('Dynamic scan wired into main()!')
else:
    print('main() pattern not found')

open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
