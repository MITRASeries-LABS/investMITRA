content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Fix 1: ATR target multiplier 3.0 -> 1.5 (closer targets, achievable in 45 min)
content = content.replace('ATR_TARGET_MULT         = 3.0', 'ATR_TARGET_MULT         = 1.5')
print('ATR target: 3.0 -> 1.5')

# Fix 2: Dead trade 45 -> 30 min (exit sooner, less brokerage drag)
content = content.replace('DEAD_TRADE_MINUTES      = 45    # Exit if no movement after 45 min',
                          'DEAD_TRADE_MINUTES      = 30    # Exit if no movement after 30 min')
print('Dead trade: 45 -> 30 min')

# Fix 3: Watchlist score threshold - lower for MICRO/SMALL
old = '''        if inv >= 60:   long_list.append(stock)
        elif inv <= 35: short_list.append(stock)
        # High quality stocks also added to short list for bearish days
        elif inv >= 55: short_list.append({**stock, "bearish_candidate": True})'''

new = '''        cap = stock.get("market_cap_category", "MID")
        # Lower threshold for MICRO/SMALL to get more small cap signals
        long_thresh  = 55 if cap in ("MICRO", "SMALL") else 60
        short_thresh = 40
        if inv >= long_thresh:   long_list.append(stock)
        elif inv <= short_thresh: short_list.append(stock)
        # High quality stocks also added to short list for bearish days
        elif inv >= 50: short_list.append({**stock, "bearish_candidate": True})'''

if old in content:
    content = content.replace(old, new)
    print('MICRO/SMALL threshold: 60 -> 55')
else:
    print('Score threshold pattern not found')

# Fix 4: Signal score requirement - lower for MICRO/SMALL
old2 = '                above_vwap and score >= 60):'
new2 = '''                above_vwap and
                score >= (55 if stock.get("market_cap_category","MID") in ("MICRO","SMALL") else 60)):'''

if old2 in content:
    content = content.replace(old2, new2)
    print('Signal score: 60 -> 55 for MICRO/SMALL')
else:
    print('Signal score pattern not found')

open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
print('All fixes applied!')
