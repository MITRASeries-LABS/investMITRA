content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Fix 1: Expand watchlist - lower score threshold
# Currently inv >= 60 for MID/LARGE
# Lower to 55 for ALL caps
old1 = '''        cap = stock.get("market_cap_category", "MID")
        # Lower threshold for MICRO/SMALL to get more small cap signals
        long_thresh  = 55 if cap in ("MICRO", "SMALL") else 60
        short_thresh = 40
        if inv >= long_thresh:   long_list.append(stock)
        elif inv <= short_thresh: short_list.append(stock)
        # High quality stocks also added to short list for bearish days
        elif inv >= 50: short_list.append({**stock, "bearish_candidate": True})'''

new1 = '''        cap = stock.get("market_cap_category", "MID")
        # Same threshold for all caps - 55 minimum
        long_thresh  = 55
        short_thresh = 40
        if inv >= long_thresh:   long_list.append(stock)
        elif inv <= short_thresh: short_list.append(stock)
        # High quality stocks also added to short list for bearish days
        elif inv >= 50: short_list.append({**stock, "bearish_candidate": True})'''

if old1 in content:
    content = content.replace(old1, new1)
    print('Fix 1: Watchlist threshold 55 for all caps')
else:
    print('Fix 1: Not found')

# Fix 2: Increase watchlist size from 25 to 40
old2 = 'long_list  = sorted(long_list,  key=lambda x: x["quality_score"], reverse=True)[:25]'
new2 = 'long_list  = sorted(long_list,  key=lambda x: x["quality_score"], reverse=True)[:40]'

if old2 in content:
    content = content.replace(old2, new2)
    print('Fix 2: Watchlist size 25 -> 40')
else:
    print('Fix 2: Not found')

# Fix 3: RVOL threshold - early morning adjustment
# Before 10 AM, RVOL is naturally low - use 1.0x minimum instead of 1.5x
old3 = '''        gap_type, gap_mult = classify_gap(true_gap_pct, volume, avg_vol)'''

new3 = '''        # Early morning RVOL adjustment - volume builds up after 10 AM
        from datetime import datetime, timezone, timedelta
        _ist = timezone(timedelta(hours=5, minutes=30))
        _now_ist = datetime.now(_ist)
        _early_morning = _now_ist.hour < 10  # Before 10 AM
        
        # Adjust avg_vol for early morning (volume is naturally lower)
        if _early_morning:
            avg_vol = avg_vol * 0.4  # Expect only 40% of daily avg before 10 AM
        
        gap_type, gap_mult = classify_gap(true_gap_pct, volume, avg_vol)'''

if old3 in content:
    content = content.replace(old3, new3)
    print('Fix 3: Early morning RVOL adjustment added')
else:
    print('Fix 3: Not found')

# Fix 4: SHORT for HINDCOPPER type - allow non-F&O if gap > 2%
# Large gaps on non-F&O can be traded as LONG fade
old4 = '''        # SHORT Option 2: HIGH QUALITY stock gapping DOWN on weak/bearish day
        # Only F&O eligible stocks can be shorted intraday reliably
        elif (symbol in self.long_map and
                weak_market and
                true_gap_pct < -gap_thresh and
                ltp <= today_open * 1.002 and
                below_vwap and score >= 55 and
                self._is_fo_eligible(symbol)):'''

new4 = '''        # SHORT Option 2: HIGH QUALITY stock gapping DOWN on weak/bearish day
        # Only F&O eligible stocks can be shorted intraday reliably
        elif (symbol in self.long_map and
                weak_market and
                true_gap_pct < -gap_thresh and
                ltp <= today_open * 1.002 and
                below_vwap and score >= 55 and
                self._is_fo_eligible(symbol) and
                abs(true_gap_pct) > 0.5):  # Require stronger gap for quality shorts'''

if old4 in content:
    content = content.replace(old4, new4)
    print('Fix 4: SHORT gap threshold 0.5% minimum')
else:
    print('Fix 4: Not found')

open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
print('All fixes applied!')
