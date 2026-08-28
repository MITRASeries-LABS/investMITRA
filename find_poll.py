content = open('scripts/order_manager.py', encoding='utf-8').read()
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'POLL' in line or 'poll' in line or 'sleep' in line.lower():
        print(f'{i+1}: {repr(line[:80])}')
