content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = 'logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")'

new = '''logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# Suppress noisy WebSocket disconnect messages
import logging as _logging
class _WSFilter(_logging.Filter):
    def filter(self, record):
        msg = str(record.getMessage())
        if 'connection was closed uncleanly' in msg: return False
        if 'peer dropped the TCP' in msg: return False
        if 'WebSocket closing handshake' in msg: return False
        return True
_logging.getLogger().addFilter(_WSFilter())'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Fixed!')
else:
    print('Pattern not found')
