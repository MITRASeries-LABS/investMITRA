content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '''        # Minimum quality filters (Sonnet recommendation)
        min_rvol = 8.0   # Minimum RVOL for any signal
        # Relaxed for Tier 2 momentum stocks
        tier = stock.get('tier', 1)
        if tier == 2:
            min_rvol = 3.0  # Tier 2 already requires gap>1%

        if rvol < min_rvol:
            return  # Skip weak volume signals'''

new = '''        # Minimum quality filters (Sonnet recommendation)
        min_rvol = 8.0   # Minimum RVOL for any signal
        tier = stock.get('tier', 1)
        if tier == 2:
            min_rvol = 3.0

        # Calculate RVOL here for filtering
        avg_vol_check = self.rvol_baseline.get(symbol, 0)
        rvol_check = min(volume / avg_vol_check if avg_vol_check > 0 else 1, 200.0)
        if rvol_check < min_rvol:
            return  # Skip weak volume signals'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Fixed!')
else:
    print('Not found')
