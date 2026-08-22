content = open('scripts/intraday_signals.py', encoding='utf-8').read()
# Find the partial exit print line
for i, line in enumerate(content.split('\n')):
    if 'PARTIAL EXIT' in line and 'print' in line:
        print(f'Line {i+1}: {repr(line)}')
