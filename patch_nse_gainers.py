content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Add NSE gainers/losers fetch function
old = 'def get_dynamic_gappers(kite, existing_symbols: set, ctx: dict) -> list[dict]:'

new = '''def get_nse_gainers_losers() -> tuple[list[str], list[str]]:
    """
    Fetch NSE top gainers and losers in real time.
    Returns (gainers, losers) symbol lists.
    Free NSE API — no rate limit.
    """
    import requests
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Accept': 'application/json',
        'Referer': 'https://www.nseindia.com'
    }
    gainers, losers = [], []
    try:
        session = requests.Session()
        session.get('https://www.nseindia.com', headers=headers, timeout=10)

        # Top gainers
        r = session.get(
            'https://www.nseindia.com/api/live-analysis-variations?index=gainers',
            headers=headers, timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            for item in data.get('NIFTY', {}).get('data', [])[:20]:
                sym = item.get('symbol','').strip()
                if sym: gainers.append(sym)
            for item in data.get('NIFTY500', {}).get('data', [])[:30]:
                sym = item.get('symbol','').strip()
                if sym and sym not in gainers: gainers.append(sym)

        # Top losers
        r = session.get(
            'https://www.nseindia.com/api/live-analysis-variations?index=loosers',
            headers=headers, timeout=10
        )
        if r.status_code == 200:
            data = r.json()
            for item in data.get('NIFTY', {}).get('data', [])[:20]:
                sym = item.get('symbol','').strip()
                if sym: losers.append(sym)
            for item in data.get('NIFTY500', {}).get('data', [])[:30]:
                sym = item.get('symbol','').strip()
                if sym and sym not in losers: losers.append(sym)

        logger.info("NSE gainers: %d | losers: %d", len(gainers), len(losers))
    except Exception as e:
        logger.warning("NSE gainers fetch failed: %s", e)

    return gainers, losers


def get_dynamic_gappers(kite, existing_symbols: set, ctx: dict) -> list[dict]:'''

if old in content:
    content = content.replace(old, new)
    print('NSE gainers function added!')
else:
    print('Pattern not found')

# Now update get_dynamic_gappers to use NSE gainers + top 200
old2 = '''        cur.execute("""
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
        """)'''

new2 = '''        # Get NSE real-time gainers/losers
        nse_gainers, nse_losers = get_nse_gainers_losers()
        nse_universe = list(set(nse_gainers + nse_losers))
        logger.info("NSE universe: %d stocks (gainers+losers)", len(nse_universe))

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
        top200 = [row[0] for row in cur.fetchall() if row[0]]

        # Combine: NSE real-time gainers/losers + top 200 by value
        all_candidates = list(set(nse_universe + top200))
        logger.info("Combined universe: %d stocks", len(all_candidates))

        # Re-fetch with combined list
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
        """)'''

if old2 in content:
    content = content.replace(old2, new2)
    print('Dynamic scan upgraded to NSE gainers+top200!')
else:
    print('Dynamic scan query pattern not found')

open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
