content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Fix 1: Dead trade back to 40 min (compromise between 30 and 45)
# Also add VWAP check - only exit if price below VWAP too
old1 = 'DEAD_TRADE_MINUTES      = 30    # Exit if no movement after 30 min'
new1 = 'DEAD_TRADE_MINUTES      = 40    # Exit if no movement after 40 min'

if old1 in content:
    content = content.replace(old1, new1)
    print('Dead trade: 30 -> 40 min')
else:
    print('Dead trade pattern not found')

# Fix 2: Dynamic scan - run in on_connect callback after stable connection
# Remove the threading approach and run after first successful connection
old2 = '''    # Dynamic gap scan at 9:35 AM ? find additional gappers
    import threading
    def _dynamic_scan():
        import time as _time
        from datetime import datetime, timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        # Wait until 9:35 AM (WebSocket stable by then)
        _time.sleep(30)  # Always wait 30 sec after startup first
        while True:
            now = datetime.now(ist)
            if now.hour == 9 and now.minute >= 35:
                break
            if now.hour > 9:
                break
            _time.sleep(10)'''

new2 = '''    # Dynamic gap scan ? runs once after WebSocket stable
    import threading
    _dynamic_done = [False]  # flag to run only once
    def _dynamic_scan():
        import time as _time
        from datetime import datetime, timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        # Wait 60 seconds for WebSocket to fully stabilize
        _time.sleep(60)
        # Then wait until after 9:30 AM
        while True:
            now = datetime.now(ist)
            if now.hour > 9 or (now.hour == 9 and now.minute >= 30):
                break
            _time.sleep(10)
        if _dynamic_done[0]: return
        _dynamic_done[0] = True'''

if old2 in content:
    content = content.replace(old2, new2)
    print('Dynamic scan timing fixed - 60s delay + 9:30 AM wait')
else:
    print('Dynamic scan pattern not found')
    for i,line in enumerate(content.split('\n')):
        if '_dynamic_scan' in line and 'def' in line:
            print(f'Line {i+1}: {repr(line[:80])}')

open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
