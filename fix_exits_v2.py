content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')

# Find gap reversal exit
for i, line in enumerate(lines):
    if 'GAP REVERSAL' in line and 'EXIT' in line and 'print' in line:
        print(f'Gap reversal at line {i+1}: {repr(line[:80])}')
    if 'gap_reversal' in line.lower() or 'gap reversal' in line.lower():
        print(f'Line {i+1}: {repr(line[:80])}')
