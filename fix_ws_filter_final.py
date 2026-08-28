content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Find and fix the filter
if '_WSFilter' in content:
    print('Filter exists - checking if applied correctly')
    for i,line in enumerate(content.split('\n')):
        if '_WSFilter' in line or 'addFilter' in line:
            print(f'Line {i+1}: {repr(line)}')
else:
    print('Filter missing - adding now')
    old = 'logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")'
    new = '''logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

class _WSFilter(logging.Filter):
    _suppress = ['uncleanly','peer dropped','WebSocket closing','1006','Connection error','Connection closed']
    def filter(self, record):
        msg = record.getMessage()
        return not any(s in msg for s in self._suppress)

for _h in logging.root.handlers:
    _h.addFilter(_WSFilter())'''

    if old in content:
        content = content.replace(old, new)
        open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
        print('Filter added!')
    else:
        print('basicConfig not found')
