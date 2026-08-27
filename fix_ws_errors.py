content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = "    ticker.on_close   = lambda ws,c,r: logger.warning(\"Closed: %s\", r)\n    ticker.on_error   = lambda ws,c,r: logger.error(\"Error: %s\", r)"

new = """    ticker.on_close        = lambda ws,c,r: logger.debug("Closed: %s", r)
    ticker.on_error        = lambda ws,c,r: logger.debug("Error: %s", r)
    ticker.on_reconnect    = on_reconnect
    ticker.on_noreconnect  = on_noreconnect"""

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('WS errors suppressed!')
else:
    print('Not found')
