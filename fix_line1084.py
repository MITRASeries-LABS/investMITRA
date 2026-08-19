content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')

# Fix lines 1083-1084 (0-indexed: 1083,1084)
print('Before fix:')
print(repr(lines[1083]))
print(repr(lines[1084]))

# Merge the two lines
lines[1083] = "    print(f\"{'='*65}\")"
lines[1084] = ''

content = '\n'.join(lines)
open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
print('Fixed!')
print('After:')
content2 = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines2 = content2.split('\n')
print(repr(lines2[1083]))
