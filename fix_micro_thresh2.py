content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '''        # GAP HOLD CONFIRMATION (5 minutes)
        gap_thresh = GAP_THRESHOLDS.get(session, 0.4)
        if self.vix_signal == "ELEVATED": gap_thresh *= 1.5'''

new = '''        # GAP HOLD CONFIRMATION (5 minutes)
        gap_thresh = GAP_THRESHOLDS.get(session, 0.4)
        # Lower threshold for MICRO/SMALL ? they gap more
        cap = stock.get("market_cap_category", "MID")
        if cap in ("MICRO", "SMALL"):
            gap_thresh *= 0.7  # 30% lower for small caps
        if self.vix_signal == "ELEVATED": gap_thresh *= 1.5'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('MICRO/SMALL threshold fixed!')
else:
    print('Not found')
