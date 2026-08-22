content = open('scripts/trade_analyzer.py', encoding='utf-8').read()

replacements = [
    ("trade.get('true_gap_pct', 0):+.2f", "float(trade.get('true_gap_pct') or 0):+.2f"),
    ("trade.get('rvol', 0):.1f", "float(trade.get('rvol') or 0):.1f"),
    ("trade.get('sector_rs', 0):.0f", "float(trade.get('sector_rs') or 0):.0f"),
    ("trade.get('sector_chg', 0):+.2f", "float(trade.get('sector_chg') or 0):+.2f"),
    ("trade.get('final_score', 0):.1f", "float(trade.get('final_score') or 0):.1f"),
    ("trade.get('quality_score', 0):.1f", "float(trade.get('quality_score') or 0):.1f"),
    ("trade.get('opp_score', 0):.1f", "float(trade.get('opp_score') or 0):.1f"),
    ("trade.get('vix_level', 0):.2f", "float(trade.get('vix_level') or 0):.2f"),
    ("trade.get('hold_minutes', 0)", "int(trade.get('hold_minutes') or 0)"),
]

fixed = 0
for old, new in replacements:
    if old in content:
        content = content.replace(old, new)
        fixed += 1

open('scripts/trade_analyzer.py', 'w', encoding='utf-8').write(content)
print(f'Fixed {fixed} replacements')
