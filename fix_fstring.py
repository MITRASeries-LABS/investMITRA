content = open('scripts/intraday_signals.py', encoding='utf-8').read()
# Fix the broken f-string split across lines
bad  = 'print(f"\n' + "{'='*65}\")"
good = "print(f\"\\n{'='*65}\")"
if bad in content:
    content = content.replace(bad, good)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Fixed!')
else:
    print('Pattern not found - checking manually...')
    lines = content.split('\n')
    for i,l in enumerate(lines):
        if 'print(f' in l and l.strip() == 'print(f"':
            print(f'Line {i+1}: {repr(l)}')
