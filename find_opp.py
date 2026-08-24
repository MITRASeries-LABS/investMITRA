content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')
for i, line in enumerate(lines):
    if 'gap_score' in line and '*' in line and 'opp' in lines[max(0,i-2):i+2][0]:
        print(f'{i+1}: {repr(line)}')
    if line.strip().startswith('opp = ('):
        print(f'OPP FOUND at line {i+1}: {repr(line)}')
