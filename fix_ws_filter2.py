content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = 'logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")'

new = '''logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

class _WSFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        if 'uncleanly' in msg: return False
        if 'peer dropped' in msg: return False
        if 'WebSocket closing handshake' in msg: return False
        if 'Connection error: 1006' in msg: return False
        if 'Connection closed: 1006' in msg: return False
        return True

logging.getLogger().addFilter(_WSFilter())'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('WebSocket filter added!')
else:
    print('Not found')
