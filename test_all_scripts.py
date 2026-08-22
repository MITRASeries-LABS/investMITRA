import subprocess, sys

scripts = [
    'python scripts/fetch_fo_stocks.py',
    'python scripts/fetch_global_sentiment.py',
    'python scripts/fetch_market_indices.py',
    'python scripts/fetch_corporate_events.py',
    'python scripts/fetch_nse_announcements.py',
    'python scripts/fetch_sebi_rss.py',
    'python scripts/trade_logger.py',
    'python scripts/daily_review.py',
]

print('Testing all scripts...\n')
for cmd in scripts:
    try:
        result = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=30)
        status = 'PASS' if result.returncode == 0 else 'FAIL'
        print(f'{status}: {cmd}')
        if result.returncode != 0:
            print(f'  Error: {result.stderr[:100]}')
    except Exception as e:
        print(f'FAIL: {cmd} ? {e}')

print('\nDone.')
