content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '''        # SHORT Option 2: HIGH QUALITY stock gapping DOWN on weak/bearish day
        # e.g. HINDCOPPER -5.6%, ATLANTAELE -1%, HAL -0.3% on bearish day
        elif (symbol in self.long_map and
                weak_market and
                true_gap_pct < -gap_thresh and
                ltp <= today_open * 1.002 and
                below_vwap and score >= 55):
            direction = "SHORT"
            logger.info("Bearish SHORT: %s gap %.2f%% breadth %.1fx", symbol, true_gap_pct, ad_ratio)'''

new = '''        # SHORT Option 2: HIGH QUALITY stock gapping DOWN on weak/bearish day
        # Only F&O eligible stocks can be shorted intraday reliably
        elif (symbol in self.long_map and
                weak_market and
                true_gap_pct < -gap_thresh and
                ltp <= today_open * 1.002 and
                below_vwap and score >= 55 and
                self._is_fo_eligible(symbol)):
            direction = "SHORT"
            logger.info("Bearish SHORT: %s gap %.2f%% breadth %.1fx (F&O eligible)", symbol, true_gap_pct, ad_ratio)'''

if old in content:
    content = content.replace(old, new)
    print('SHORT F&O check added!')
else:
    print('Pattern not found')

# Add _is_fo_eligible method to IntradayEngine class
old2 = '    def _save_trades_to_neon(self):'

new2 = '''    def _is_fo_eligible(self, symbol: str) -> bool:
        """Check if stock is F&O eligible (can be shorted intraday)."""
        try:
            conn = psycopg2.connect(NEON_URL, connect_timeout=5)
            cur  = conn.cursor()
            cur.execute("SELECT 1 FROM investmitra.fo_stocks WHERE symbol=%s", (symbol,))
            result = cur.fetchone() is not None
            cur.close(); conn.close()
            return result
        except:
            return True  # Default allow if DB check fails

    def _save_trades_to_neon(self):'''

if old2 in content:
    content = content.replace(old2, new2)
    print('_is_fo_eligible method added!')
else:
    print('_is_fo_eligible pattern not found')

open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
