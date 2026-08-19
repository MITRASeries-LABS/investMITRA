content = open('scripts/intraday_signals.py', encoding='utf-8').read()
lines = content.split('\n')

# Find all broken f-strings (line ends with just print(f" or similar)
broken = []
for i, line in enumerate(lines):
    stripped = line.strip()
    if stripped in ('print(f"', "print(f'"):
        broken.append(i)
        print(f'Broken f-string at line {i+1}: {repr(line)}')
        print(f'  Next line: {repr(lines[i+1])}')

print(f'Total broken: {len(broken)}')

# Fix each one - join with next line
if broken:
    new_lines = []
    skip_next = set()
    for i, line in enumerate(lines):
        if i in skip_next:
            continue
        if i in broken:
            # Merge this line with next
            merged = line.rstrip() + lines[i+1].lstrip()
            new_lines.append(merged)
            skip_next.add(i+1)
        else:
            new_lines.append(line)
    
    content = '\n'.join(new_lines)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('All fixed!')
