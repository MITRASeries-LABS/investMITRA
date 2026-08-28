content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Fix: use correct KiteTicker reconnect parameters
old = 'ticker = KiteTicker(API_KEY, ACCESS_TOKEN, reconnect=True, reconnect_max_tries=300)'
new = 'ticker = KiteTicker(API_KEY, ACCESS_TOKEN, reconnect=True, reconnect_max_tries=300, reconnect_max_delay=5)'

if old in content:
    content = content.replace(old, new)
    print('Reconnect params updated')
else:
    # Find current KiteTicker line
    for i, line in enumerate(content.split('\n')):
        if 'KiteTicker(' in line:
            print(f'Line {i+1}: {repr(line)}')

# Fix: suppress error logs properly
old2 = '    ticker.on_close        = lambda ws,c,r: logger.debug("Closed: %s", r)\n    ticker.on_error        = lambda ws,c,r: logger.debug("Error: %s", r)'
new2 = '    ticker.on_close        = lambda ws,c,r: None\n    ticker.on_error        = lambda ws,c,r: None'

if old2 in content:
    content = content.replace(old2, new2)
    print('Error logs suppressed completely')
else:
    print('Error log pattern not found')

open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
