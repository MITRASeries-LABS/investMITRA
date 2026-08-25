content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Fix 1: On bearish days, add high quality stocks to short list too
old1 = '''        if inv >= 60:   long_list.append(stock)
        elif inv <= 35: short_list.append(stock)'''

new1 = '''        if inv >= 60:   long_list.append(stock)
        elif inv <= 35: short_list.append(stock)
        # High quality stocks also added to short list for bearish days
        elif inv >= 55: short_list.append({**stock, "bearish_candidate": True})'''

if old1 in content:
    content = content.replace(old1, new1)
    print('Fix 1: short list expanded!')
else:
    print('Fix 1: not found')

# Fix 2: On weak breadth days, allow quality stocks to short
old2 = '''    if market_direction == "BULLISH":   short_list = []
    elif market_direction == "BEARISH": long_list  = []'''

new2 = '''    # On weak breadth days ? allow quality stocks to go SHORT too
    breadth = ctx.get("breadth", {})
    adv_ratio = breadth.get("adv_ratio", 1.0) if isinstance(breadth, dict) else 1.0
    weak_market = adv_ratio < 0.3 or market_direction == "BEARISH"

    if market_direction == "BULLISH":
        short_list = []
    elif market_direction == "BEARISH":
        long_list = []
    elif weak_market:
        # NEUTRAL but weak breadth ? keep both but flag bearish bias
        logger.info("Weak breadth (%.1fx) ? SHORT bias enabled for quality stocks", adv_ratio)'''

if old2 in content:
    content = content.replace(old2, new2)
    print('Fix 2: weak breadth SHORT bias added!')
else:
    print('Fix 2: not found')

open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
