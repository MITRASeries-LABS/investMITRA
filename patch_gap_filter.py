content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Find and update classify_gap function
old = '''def classify_gap(gap_pct, volume, avg_volume) -> tuple[str, float]:
    abs_gap  = abs(gap_pct)
    high_vol = (volume / avg_volume if avg_volume > 0 else 1) > 1.5
    if abs_gap > 4.0:                            return "exhaustion", 0.5
    elif abs_gap > 2.0 and high_vol:             return "continuation_strong", 1.3
    elif abs_gap > 2.0:                          return "fade_risk", 0.6
    elif abs_gap > 0.5 and high_vol:             return "continuation", 1.1
    elif abs_gap > 0.3 and high_vol:             return "continuation", 1.0
    elif abs_gap > 0.3:                          return "fade_risk", 0.8
    else:                                         return "small_gap", 0.7'''

new = '''def classify_gap(gap_pct, volume, avg_volume) -> tuple[str, float]:
    abs_gap  = abs(gap_pct)
    rvol     = volume / avg_volume if avg_volume > 0 else 1
    high_vol = rvol > 1.5
    strong_vol = rvol > 2.5  # Strong institutional interest

    if abs_gap > 4.0:
        return "exhaustion", 0.4       # Too big — likely to reverse

    elif abs_gap > 2.0 and strong_vol:
        return "continuation_strong", 1.3  # Large gap + strong volume = best

    elif abs_gap > 2.0 and high_vol:
        return "continuation", 1.1     # Large gap + decent volume

    elif abs_gap > 2.0:
        return "exhaustion", 0.4       # Large gap + weak volume = exhaustion

    elif abs_gap > 0.5 and strong_vol:
        return "continuation", 1.1     # Medium gap + strong volume

    elif abs_gap > 0.3 and high_vol:
        return "continuation", 1.0     # Normal gap + volume confirmed

    elif abs_gap > 0.3 and strong_vol:
        return "continuation", 1.0     # Normal gap + strong volume

    elif abs_gap > 0.5:
        return "fade_risk", 0.5        # Medium gap + weak volume — reduce score heavily

    elif abs_gap > 0.3:
        return "fade_risk", 0.3        # Small gap + weak volume — almost skip

    else:
        return "small_gap", 0.2        # Too small — skip'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('classify_gap updated!')
else:
    print('Pattern not found')
    for i,line in enumerate(content.split('\n')):
        if 'def classify_gap' in line:
            print(f'Line {i+1}: {repr(line)}')

# Also update the fade_risk check in _check_signal
# Currently: if gap_type in ("exhaustion", "fade_risk"): return for fade_risk
# Change to: allow fade_risk only if RVOL > 2.5x

old2 = '''        if details["gap_type"] in ("exhaustion", "fade_risk"):
            if details["gap_type"] == "exhaustion": return
            # fade_risk: require higher quality + volume
            if quality < 65 or details["rvol"] < 1.5: return'''

new2 = '''        # Exhaustion gaps: always skip
        if details["gap_type"] == "exhaustion":
            return

        # fade_risk: only allow if RVOL > 2.5x AND quality > 65
        # Otherwise skip — Sonnet confirmed fade_risk consistently loses
        if details["gap_type"] == "fade_risk":
            if details["rvol"] < 2.5 or quality < 65:
                logger.debug("Skip %s — fade_risk with weak RVOL %.1fx", symbol, details["rvol"])
                return'''

if old2 in content:
    content = content.replace(old2, new2)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('fade_risk filter updated!')
else:
    print('fade_risk pattern not found')
    for i,line in enumerate(content.split('\n')):
        if 'fade_risk' in line and 'gap_type' in line:
            print(f'Line {i+1}: {repr(line)}')
