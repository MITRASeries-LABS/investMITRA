content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Find and replace the F&O filter line
old = "          AND cm.nse_symbol IN (SELECT symbol FROM investmitra.fo_stocks)"

new = """          AND (
            -- Tier 1: F&O eligible stocks (most liquid)
            cm.nse_symbol IN (SELECT symbol FROM investmitra.fo_stocks)
            OR
            -- Tier 2: High volume small/mid caps not in F&O
            (cm.market_cap_category IN ('SMALL','MICRO')
             AND cm.nse_symbol NOT IN (SELECT symbol FROM investmitra.fo_stocks))
          )"""

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Two-tier watchlist added!')
else:
    print('Pattern not found — checking...')
    # Find the fo_stocks reference
    for i, line in enumerate(content.split('\n')):
        if 'fo_stocks' in line:
            print(f'Line {i+1}: {repr(line)}')
