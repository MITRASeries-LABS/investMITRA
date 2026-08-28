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

new = '''    # Dynamic gap scan at 9:35 AM ? find additional gappers
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

if old in content:
    content = content.replace(old, new)
    print('Timing fixed - scan at 9:35 AM!')
else:
    print('Pattern not found')

# Also fix the token subscription - do it safely
old2 = '''            if new_tokens:
                logger.info("Subscribing %d dynamic gapper tokens", len(new_tokens))
                # Will subscribe on next reconnect or via ticker
                try:
                    ticker.subscribe(new_tokens)
                    ticker.set_mode(ticker.MODE_FULL, new_tokens)
                except: pass'''

new2 = '''            if new_tokens:
                logger.info("Subscribing %d dynamic gapper tokens", len(new_tokens))
                import time as _t
                _t.sleep(2)  # Wait for WebSocket stability
                try:
                    ticker.subscribe(new_tokens)
                    ticker.set_mode(ticker.MODE_FULL, new_tokens)
                    _t.sleep(1)
                except Exception as e:
                    logger.warning("Token subscribe failed: %s", e)'''

if old2 in content:
    content = content.replace(old2, new2)
    print('Token subscription stabilized!')
else:
    print('Token pattern not found')

open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
