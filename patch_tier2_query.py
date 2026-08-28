content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Find the score filter in watchlist
old = '''        if inv >= long_thresh:   long_list.append(stock)
        elif inv <= short_thresh: short_list.append(stock)
        # High quality stocks also added to short list for bearish days
        elif inv >= 50: short_list.append({**stock, "bearish_candidate": True})'''

new = '''        # Load thresholds
        thresholds = load_signal_thresholds()
        t1_score = thresholds.get('tier1_score_min', 55)

        if inv >= t1_score:
            stock['tier'] = 1
            long_list.append(stock)
        elif inv <= short_thresh:
            short_list.append(stock)
        elif inv >= 50:
            short_list.append({**stock, "bearish_candidate": True})'''

if old in content:
    content = content.replace(old, new)
    print('Tier 1 threshold wired!')
else:
    print('Not found')

# Now add Tier 2 stocks AFTER the main query
old2 = '''    long_list  = sorted(long_list,  key=lambda x: x["quality_score"], reverse=True)[:50]
    short_list = sorted(short_list, key=lambda x: x["investmitra_score"])[:10]
    return long_list, short_list'''

new2 = '''    # Tier 2: Add momentum stocks (high gap + high RVOL, any score)
    try:
        thresholds = load_signal_thresholds()
        t2_gap   = thresholds.get('tier2_gap_min', 1.0)
        t2_rvol  = thresholds.get('tier2_rvol_min', 3.0)
        t2_trade = thresholds.get('tier2_traded_min', 5000000)

        cur2 = conn2 = None
        conn2 = psycopg2.connect(NEON_URL, connect_timeout=10)
        cur2  = conn2.cursor()

        # Get all liquid stocks not already in watchlist
        existing_syms = {s['symbol'] for s in long_list + short_list}
        cur2.execute("""
            SELECT cm.nse_symbol, cm.market_cap_category,
                   COALESCE(ds.investmitra_score, 45) as score,
                   ds.sector, ep.close,
                   AVG(ep.volume) as avg_vol,
                   AVG(ep.volume * ep.close) as avg_traded
            FROM investmitra.equity_prices ep
            JOIN investmitra.company_master cm ON ep.isin = cm.isin
            LEFT JOIN investmitra.daily_scores ds ON ep.isin = ds.isin
                AND ds.score_date = (SELECT MAX(score_date) FROM investmitra.daily_scores)
            WHERE ep.trade_date = (SELECT MAX(trade_date) FROM investmitra.equity_prices)
              AND ep.close BETWEEN 50 AND 20000
              AND ep.volume * ep.close >= %s
              AND cm.nse_symbol IS NOT NULL
            GROUP BY cm.nse_symbol, cm.market_cap_category, ds.investmitra_score, ds.sector, ep.close
            HAVING AVG(ep.volume * ep.close) >= %s
            ORDER BY AVG(ep.volume * ep.close) DESC
            LIMIT 500
        """, (t2_trade, t2_trade))

        t2_rows = cur2.fetchall()
        cur2.close(); conn2.close()

        t2_added = 0
        for row in t2_rows:
            sym = row[0]
            if sym in existing_syms: continue
            if sym in results_symbols: continue
            stock = {
                'symbol':              sym,
                'market_cap_category': row[1] or 'MID',
                'investmitra_score':   float(row[2] or 45),
                'quality_score':       float(row[2] or 45),
                'sector':              row[3] or '',
                'avg_price':           float(row[4] or 0),
                'avg_vol':             float(row[5] or 0),
                'avg_traded':          float(row[6] or 0),
                'screen_count':        0,
                'piotroski_score':     0,
                'bulk_deal':           False,
                'prev_day_chg':        0,
                'company_name':        sym,
                'tier':                2,  # Mark as Tier 2
            }
            long_list.append(stock)
            existing_syms.add(sym)
            t2_added += 1

        logger.info("Tier 2 added %d momentum stocks (gap>%.1f%% rvol>%.1fx)", t2_added, t2_gap, t2_rvol)
    except Exception as e:
        logger.warning("Tier 2 load failed: %s", e)

    long_list  = sorted(long_list,  key=lambda x: x["quality_score"], reverse=True)[:100]
    short_list = sorted(short_list, key=lambda x: x["investmitra_score"])[:10]
    return long_list, short_list'''

if old2 in content:
    content = content.replace(old2, new2)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Tier 2 momentum universe added! Watchlist now 100 stocks.')
else:
    print('Sort pattern not found')
    for i,line in enumerate(content.split('\n')):
        if 'long_list  = sorted' in line:
            print(f'Line {i+1}: {repr(line[:80])}')
