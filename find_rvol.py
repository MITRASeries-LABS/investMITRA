content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'def get_rvol' in line or 'rvol_baseline' in line.lower() and 'def' in line:
        print(f'{i+1}: {repr(line)}')
