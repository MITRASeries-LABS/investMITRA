content = open('scripts/order_manager.py', encoding='utf-8').read()

old = 'POLL_INTERVAL_SEC      = 5    # Check positions every 5 seconds'
new = 'POLL_INTERVAL_SEC      = 15   # Check positions every 15 seconds'

if old in content:
    content = content.replace(old, new)
    open('scripts/order_manager.py', 'w', encoding='utf-8').write(content)
    print('Poll interval: 5s -> 15s')
else:
    print('Not found')
