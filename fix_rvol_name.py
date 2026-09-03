content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Fix 1: rvol_score_raw not defined - use rvol directly
old = 'if rvol_score_raw < min_rvol:'
new = 'if rvol < min_rvol:'

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('rvol_score_raw fixed!')
else:
    print('Not found')
