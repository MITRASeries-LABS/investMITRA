content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '''    # Dynamic gap scan at 9:32 AM ? find additional gappers
    import threading
    def _dynamic_scan():
        import time as _time
        from datetime import datetime, timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        # Wait until 9:32 AM
        while True:
            now = datetime.now(ist)
            if now.hour == 9 and now.minute >= 32:
                break
            if now.hour > 9:
                break
            _time.sleep(10)'''

new = '''    # Dynamic gap scan ? runs once after WebSocket stable
    import threading
    _dynamic_done = [False]
    def _dynamic_scan():
        import time as _time
        # Wait 60 seconds for WebSocket to fully stabilize
        _time.sleep(60)
        # Then wait until after 9:30 AM
        while True:
            now = datetime.now(IST)
            if now.hour > 9 or (now.hour == 9 and now.minute >= 30):
                break
            _time.sleep(10)
        if _dynamic_done[0]: return
        _dynamic_done[0] = True'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Dynamic scan timing fixed!')
else:
    print('Not found - check whitespace')
    idx = content.find('# Dynamic gap scan at 9:32')
    print(repr(content[idx:idx+300]))
