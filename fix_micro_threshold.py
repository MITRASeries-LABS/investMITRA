content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '''        abs_gap = abs(true_gap_pct)
        if session == "momentum" and abs_gap < GAP_THRESHOLDS["momentum"]: return
        if session == "choppy"   and abs_gap < GAP_THRESHOLDS["choppy"]:   return
        if session == "afternoon"and abs_gap < GAP_THRESHOLDS["afternoon"]: return'''

new = '''        abs_gap = abs(true_gap_pct)
        # Lower threshold for MICRO/SMALL caps ? they gap more
        cap = stock.get("market_cap_category", "MID")
        if cap in ("MICRO", "SMALL"):
            gap_mult = 0.7  # 30% lower threshold for small caps
        else:
            gap_mult = 1.0
        if session == "momentum" and abs_gap < GAP_THRESHOLDS["momentum"] * gap_mult: return
        if session == "choppy"   and abs_gap < GAP_THRESHOLDS["choppy"]   * gap_mult: return
        if session == "afternoon"and abs_gap < GAP_THRESHOLDS["afternoon"]* gap_mult: return'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('MICRO/SMALL threshold fixed!')
else:
    print('Pattern not found')
    for i,line in enumerate(content.split('\n')):
        if 'GAP_THRESHOLDS' in line and 'abs_gap' in line:
            print(f'Line {i+1}: {repr(line)}')
