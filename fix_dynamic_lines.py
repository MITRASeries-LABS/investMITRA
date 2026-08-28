content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')

# Replace lines 1618-1631 with new timing logic
new_lines = [
    '    # Dynamic gap scan ? runs once after WebSocket stable',
    '    import threading',
    '    _dynamic_done = [False]',
    '    def _dynamic_scan():',
    '        import time as _time',
    '        # Wait 60 seconds for WebSocket to fully stabilize',
    '        _time.sleep(60)',
    '        # Then wait until after 9:30 AM',
    '        while True:',
    '            now = datetime.now(IST)',
    '            if now.hour > 9 or (now.hour == 9 and now.minute >= 30):',
    '                break',
    '            _time.sleep(10)',
    '        if _dynamic_done[0]: return',
    '        _dynamic_done[0] = True',
]

# Find the lines to replace (1618-1631, 0-indexed 1617-1630)
lines[1617:1631] = new_lines

content = '\n'.join(lines)
open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
print('Fixed!')
# Verify
lines2 = content.split('\n')
for i in range(1617, 1633):
    print(f'{i+1}: {lines2[i]}')
