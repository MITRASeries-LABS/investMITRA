content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Add minimum RVOL and sector RS filter in _check_signal
old = '''        direction = None

        # LONG: quality stock gapping up in neutral/bullish market
        if (symbol in self.long_map and
                self.market_direction in ("BULLISH","NEUTRAL") and
                true_gap_pct > gap_thresh and
                ltp >= today_open * 0.998 and
                above_vwap and
                score >= (55 if stock.get("market_cap_category","MID") in ("MICRO","SMALL") else 60)):
            direction = "LONG"'''

new = '''        direction = None

        # Minimum quality filters (Sonnet recommendation)
        min_rvol = 8.0   # Minimum RVOL for any signal
        # Relaxed for Tier 2 momentum stocks
        tier = stock.get('tier', 1)
        if tier == 2:
            min_rvol = 3.0  # Tier 2 already requires gap>1%

        if rvol_score_raw < min_rvol:
            return  # Skip weak volume signals

        # LONG: quality stock gapping up in neutral/bullish market
        if (symbol in self.long_map and
                self.market_direction in ("BULLISH","NEUTRAL") and
                true_gap_pct > gap_thresh and
                ltp >= today_open * 0.998 and
                above_vwap and
                score >= (55 if stock.get("market_cap_category","MID") in ("MICRO","SMALL") else 60)):
            direction = "LONG"'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('RVOL filter added!')
else:
    print('Pattern not found')
    for i,line in enumerate(content.split('\n')):
        if 'direction = None' in line and 'LONG' in content.split('\n')[i+3]:
            print(f'Line {i+1}: {repr(line)}')
