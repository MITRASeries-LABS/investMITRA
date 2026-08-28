content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '''        # Early morning RVOL adjustment - volume builds up after 10 AM
        from datetime import datetime, timezone, timedelta
        _ist = timezone(timedelta(hours=5, minutes=30))
        _now_ist = datetime.now(_ist)
        _early_morning = _now_ist.hour < 10  # Before 10 AM
        
        # Adjust avg_vol for early morning (volume is naturally lower)
        if _early_morning:
            avg_vol = avg_vol * 0.4  # Expect only 40% of daily avg before 10 AM'''

new = '''        # Early morning RVOL adjustment - volume builds up after 10 AM
        _now_ist = datetime.now(IST)
        _early_morning = _now_ist.hour < 10  # Before 10 AM
        
        # Adjust avg_vol for early morning (volume is naturally lower)
        if _early_morning:
            avg_vol = avg_vol * 0.4  # Expect only 40% of daily avg before 10 AM'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Fixed!')
else:
    print('Not found')
