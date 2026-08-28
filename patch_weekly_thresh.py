content = open('scripts/weekly_review.py', encoding='utf-8').read()

old = '        from weight_optimizer import run_weekly_review'
new = '        from weight_optimizer import run_weekly_review, update_signal_thresholds'

if old in content:
    content = content.replace(old, new)
    print('Import updated!')
else:
    # Try alternate
    for i,line in enumerate(content.split('\n')):
        if 'weight_optimizer' in line:
            print(f'Line {i+1}: {repr(line)}')

old2 = '        run_weekly_review(weeks=1)'
new2 = '''        run_weekly_review(weeks=1)
        # Update self-learning thresholds
        from trade_logger import get_recent_trades
        trades = get_recent_trades(days=7)
        update_signal_thresholds({'total': len(trades)}, trades)'''

if old2 in content:
    content = content.replace(old2, new2)
    print('Threshold update wired into weekly review!')
else:
    print('run_weekly_review pattern not found')

open('scripts/weekly_review.py', 'w', encoding='utf-8').write(content)
