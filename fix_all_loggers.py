content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '''class _WSFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        if 'uncleanly' in msg: return False
        if 'peer dropped' in msg: return False
        if 'WebSocket closing handshake' in msg: return False
        if 'Connection error: 1006' in msg: return False
        if 'Connection closed: 1006' in msg: return False
        return True

logging.getLogger().addFilter(_WSFilter())'''

new = '''class _WSFilter(logging.Filter):
    _suppress = ['uncleanly','peer dropped','WebSocket closing',
                 'Connection error: 1006','Connection closed: 1006',
                 'Unhandled Error','frame_data']
    def filter(self, record):
        msg = record.getMessage()
        return not any(s in msg for s in self._suppress)

# Apply to ALL loggers including Kite/Twisted
_ws_filter = _WSFilter()
logging.getLogger().addFilter(_ws_filter)
logging.getLogger('kiteconnect').addFilter(_ws_filter)
logging.getLogger('twisted').addFilter(_ws_filter)
logging.getLogger('autobahn').addFilter(_ws_filter)
# Also set higher level for noisy loggers
logging.getLogger('kiteconnect.ticker').setLevel(logging.CRITICAL)
logging.getLogger('twisted').setLevel(logging.CRITICAL)'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('All loggers filtered!')
else:
    print('Pattern not found')
