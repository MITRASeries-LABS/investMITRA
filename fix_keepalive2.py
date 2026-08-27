content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '''    def on_connect(ws, response):
        logger.info("Connected \u2014 %d tokens", len(tokens))
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)

    ticker = KiteTicker(API_KEY, ACCESS_TOKEN)'''

new = '''    def on_connect(ws, response):
        logger.info("Connected \u2014 %d tokens", len(tokens))
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)

    def on_reconnect(ws, attempts):
        logger.info("Reconnecting... attempt %d", attempts)

    def on_noreconnect(ws):
        logger.error("Max reconnects reached")

    import threading, time
    def _keepalive(ws_ref):
        while True:
            time.sleep(240)  # ping every 4 minutes
            try:
                if hasattr(ws_ref, '_ws') and ws_ref._ws:
                    ws_ref._ws.ping()
                    logger.debug("Keepalive ping sent")
            except: pass

    ticker = KiteTicker(API_KEY, ACCESS_TOKEN, reconnect=True, reconnect_max_tries=300)'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Keepalive fixed!')
else:
    print('Not found - trying unicode')
    idx = content.find('def on_connect(ws, response):')
    print(repr(content[idx:idx+200]))
