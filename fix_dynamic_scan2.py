content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = 'def get_rvol_baseline() -> dict[str, float]:'

new = '''def get_dynamic_gappers(kite, existing_symbols: set, ctx: dict) -> list[dict]:
    """
    Dynamic gap scanner ? runs at 9:32 AM after opens captured.
    Scans top 200 NSE stocks by traded value for genuine gaps.
    Adds any gapping stock not already in watchlist.
    """
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=15)
        cur  = conn.cursor()

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

        candidates = []
        for row in rows:
            sym = row[0]
            if sym in existing_symbols or not sym: continue
            candidates.append({
                'symbol':              sym,
                'market_cap_category': row[1] or 'MID',
                'investmitra_score':   float(row[2] or 50),
                'sector':              row[3] or '',
                'prev_close':          float(row[4] or 0),
                'avg_vol':             float(row[5] or 0),
                'avg_traded':          float(row[6] or 0),
                'quality_score':       float(row[2] or 50),
                'screen_count':        0,
                'piotroski_score':     0,
                'bulk_deal':           False,
                'prev_day_chg':        0,
                'company_name':        sym,
            })

        if not candidates:
            return []

        results_today = ctx.get('results_today', set())
        dynamic_gappers = []

        for i in range(0, len(candidates), 50):
            batch = candidates[i:i+50]
            batch_syms = [c['symbol'] for c in batch]
            try:
                quotes = kite.quote([f"NSE:{s}" for s in batch_syms])
            except:
                continue

            for c in batch:
                sym = c['symbol']
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

                from datetime import datetime, timezone, timedelta
                _ist   = timezone(timedelta(hours=5, minutes=30))
                _early = datetime.now(_ist).hour < 10
                if _early: avg_vol *= 0.4

                gap_type, _ = classify_gap(gap_pct, vol, avg_vol)
                cap    = c['market_cap_category']
                thresh = GAP_THRESHOLDS.get('momentum', 0.30)
                if cap in ('MICRO','SMALL'): thresh *= 0.7

                if (abs(gap_pct) >= thresh and
                        gap_type not in ('exhaustion','fade_risk','small_gap')):
                    c['gap_pct']  = gap_pct
                    c['gap_type'] = gap_type
                    c['ltp']      = ltp
                    dynamic_gappers.append(c)
                    logger.info("Dynamic gapper: %s gap=%.2f%% (%s)", sym, gap_pct, gap_type)

        logger.info("Dynamic scan: %d new gappers from top-200", len(dynamic_gappers))
        return dynamic_gappers[:20]

    except Exception as e:
        logger.warning("Dynamic gap scan failed: %s", e)
        return []


def get_rvol_baseline() -> dict[str, float]:'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Dynamic scanner added!')
else:
    print('Not found')
