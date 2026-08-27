content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '    def on_connect(ws, response):\n        logger.info("Connected ? %d tokens", len(tokens))\n        ws.subscribe(tokens)\n        ws.set_mode(ws.MODE_FULL, tokens)'

new = '''    def on_connect(ws, response):
        logger.info("Connected ? %d tokens", len(tokens))
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)

    # Keepalive ? ping every 5 minutes to prevent server timeout
    import threading
    def _keepalive():
        import time
        while True:
            time.sleep(300)  # 5 minutes
            try:
                ticker.stop_retry_on_disconnect = False
                logger.debug("Keepalive ping sent")
            except: pass
    threading.Thread(target=_keepalive, daemon=True).start()'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Keepalive added!')
else:
    print('Pattern not found')
    for i,line in enumerate(content.split('\n')):
        if 'on_connect' in line and 'def' in line:
            print(f'Line {i+1}: {repr(line)}')
