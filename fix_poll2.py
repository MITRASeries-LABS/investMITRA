content = open('scripts/order_manager.py', encoding='utf-8').read()
content = content.replace('POLL_INTERVAL_SEC     = 5', 'POLL_INTERVAL_SEC     = 15')
open('scripts/order_manager.py', 'w', encoding='utf-8').write(content)
print('Poll interval: 5s -> 15s')
