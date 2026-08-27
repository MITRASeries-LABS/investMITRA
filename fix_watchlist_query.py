content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Fix 1: Remove F&O restriction - include ALL MID/LARGE stocks
old1 = '''          AND (
            -- Tier 1: F&O eligible stocks (most liquid)
            cm.nse_symbol IN (SELECT symbol FROM investmitra.fo_stocks)
            OR
            -- Tier 2: High volume small/mid caps not in F&O
            (cm.market_cap_category IN ('SMALL','MICRO')
             AND cm.nse_symbol NOT IN (SELECT symbol FROM investmitra.fo_stocks))
          )'''

new1 = '''          -- All cap categories included
          -- F&O check only applied to SHORT signals (in _check_signal)'''

if old1 in content:
    content = content.replace(old1, new1)
    print('Fix 1: F&O restriction removed from watchlist')
else:
    print('Fix 1: Not found')

# Fix 2: Lower traded value threshold
old2 = '               AND AVG(volume)*AVG(close) >= 5000000'
new2 = '               AND AVG(volume)*AVG(close) >= 2000000'

if old2 in content:
    content = content.replace(old2, new2)
    print('Fix 2: Traded value 50L -> 20L')
else:
    print('Fix 2: Not found')

# Fix 3: Increase watchlist size to 50
old3 = 'long_list  = sorted(long_list,  key=lambda x: x["quality_score"], reverse=True)[:40]'
new3 = 'long_list  = sorted(long_list,  key=lambda x: x["quality_score"], reverse=True)[:50]'

if old3 in content:
    content = content.replace(old3, new3)
    print('Fix 3: Watchlist size -> 50')
else:
    old3b = 'long_list  = sorted(long_list,  key=lambda x: x["quality_score"], reverse=True)[:25]'
    new3b = 'long_list  = sorted(long_list,  key=lambda x: x["quality_score"], reverse=True)[:50]'
    if old3b in content:
        content = content.replace(old3b, new3b)
        print('Fix 3: Watchlist size 25 -> 50')
    else:
        print('Fix 3: Not found')

open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
print('All fixes applied!')
