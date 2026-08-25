content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Find the logging setup line
for i, line in enumerate(content.split('\n')):
    if 'logging.basicConfig' in line:
        print(f'Line {i+1}: {repr(line)}')
