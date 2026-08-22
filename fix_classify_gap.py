content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '''def classify_gap(gap_pct, volume, avg_volume) -> tuple[str, float]:
    abs_gap  = abs(gap_pct)
    high_vol = (volume / avg_volume if avg_volume > 0 else 1) > 1.5
    if abs_gap > 4.0:                            return "exhaustion", 0.4
    elif abs_gap > 2.0 and high_vol:             return "continuation_strong", 1.3
    elif abs_gap > 2.0:                          return "fade_risk", 0.6
    elif abs_gap > 0.5 and high_vol:             return "continuation", 1.1
    elif abs_gap > 0.3 and high_vol:             return "continuation", 1.0
    elif abs_gap > 0.3:                          return "fade_risk", 0.8
    else:                                         return "small_gap", 0.7'''

new = '''def classify_gap(gap_pct, volume, avg_volume) -> tuple[str, float]:
    abs_gap    = abs(gap_pct)
    rvol       = volume / avg_volume if avg_volume > 0 else 1
    high_vol   = rvol > 1.5
    strong_vol = rvol > 2.5
    if abs_gap > 4.0:                              return "exhaustion", 0.4
    elif abs_gap > 2.0 and strong_vol:             return "continuation_strong", 1.3
    elif abs_gap > 2.0 and high_vol:               return "continuation", 1.1
    elif abs_gap > 2.0:                            return "exhaustion", 0.4
    elif abs_gap > 0.5 and strong_vol:             return "continuation", 1.1
    elif abs_gap > 0.3 and high_vol:               return "continuation", 1.0
    elif abs_gap > 0.5:                            return "fade_risk", 0.5
    elif abs_gap > 0.3:                            return "fade_risk", 0.3
    else:                                          return "small_gap", 0.2'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('classify_gap updated!')
else:
    print('Still not found - check whitespace')
    idx = content.find('def classify_gap')
    print(repr(content[idx:idx+300]))
