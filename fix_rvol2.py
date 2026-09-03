content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Fix 1: classify_gap function
old1 = '    rvol       = volume / avg_volume if avg_volume > 0 else 1'
new1 = '    rvol       = min(volume / avg_volume if avg_volume > 0 else 1, 200.0)'

# Fix 2: compute_opportunity_score
old2 = '        avg_daily  = self.rvol_baseline.get(isin, stock.get("avg_volume", 1))'
new2 = '        avg_daily  = max(self.rvol_baseline.get(isin, stock.get("avg_volume", 1)), 1)'

fixed = 0
if old1 in content:
    content = content.replace(old1, new1)
    print('Fix 1: classify_gap RVOL capped at 200x')
    fixed += 1

if old2 in content:
    content = content.replace(old2, new2)
    print('Fix 2: avg_daily minimum 1 (no division by zero)')
    fixed += 1

# Also cap rvol in opportunity score
old3 = '        rvol       = volume / avg_daily if avg_daily > 0 else 1'
new3 = '        rvol       = min(volume / avg_daily if avg_daily > 0 else 1, 200.0)'

if old3 in content:
    content = content.replace(old3, new3)
    print('Fix 3: opportunity score RVOL capped at 200x')
    fixed += 1

open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
print(f'Total fixes: {fixed}')
