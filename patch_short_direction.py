content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '''        direction = None
        if (symbol in self.long_map and
                self.market_direction in ("BULLISH","NEUTRAL") and
                true_gap_pct > gap_thresh and
                ltp >= today_open * 0.998 and  # holding above open
                above_vwap and score >= 60):
            direction = "LONG"
        elif (symbol in self.short_map and
                self.market_direction in ("BEARISH","NEUTRAL") and
                true_gap_pct < -gap_thresh and
                ltp <= today_open * 1.002 and  # holding below open
                below_vwap and score <= 40):
            direction = "SHORT"
        if not direction: return'''

new = '''        # Check market breadth for bearish bias
        breadth     = self.ctx.get("breadth", {})
        ad_ratio    = breadth.get("adv_ratio", 1.0) if isinstance(breadth, dict) else 1.0
        weak_market = ad_ratio < 0.3 or self.market_direction == "BEARISH"

        direction = None

        # LONG: quality stock gapping up in neutral/bullish market
        if (symbol in self.long_map and
                self.market_direction in ("BULLISH","NEUTRAL") and
                true_gap_pct > gap_thresh and
                ltp >= today_open * 0.998 and
                above_vwap and score >= 60):
            direction = "LONG"

        # SHORT Option 1: dedicated short stock (low quality) gapping down
        elif (symbol in self.short_map and
                self.market_direction in ("BEARISH","NEUTRAL") and
                true_gap_pct < -gap_thresh and
                ltp <= today_open * 1.002 and
                below_vwap and score <= 40):
            direction = "SHORT"

        # SHORT Option 2: HIGH QUALITY stock gapping DOWN on weak/bearish day
        # e.g. HINDCOPPER -5.6%, ATLANTAELE -1%, HAL -0.3% on bearish day
        elif (symbol in self.long_map and
                weak_market and
                true_gap_pct < -gap_thresh and
                ltp <= today_open * 1.002 and
                below_vwap and score >= 55):
            direction = "SHORT"
            logger.info("Bearish SHORT: %s gap %.2f%% breadth %.1fx", symbol, true_gap_pct, ad_ratio)

        if not direction: return'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('SHORT direction fix applied!')
else:
    print('Pattern not found')
    # Find approximate location
    idx = content.find('direction = None')
    print(repr(content[idx:idx+400]))
