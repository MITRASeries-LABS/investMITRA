content = open('scripts/trade_analyzer.py', encoding='utf-8').read()
# Fix None formatting in the prompt
old = 'f"  True Gap:      {trade.get(\'true_gap_pct\', 0):+.2f}%'
new = 'f"  True Gap:      {float(trade.get(\'true_gap_pct\') or 0):+.2f}%'
if old in content:
    content = content.replace(old, new)
    open('scripts/trade_analyzer.py', 'w', encoding='utf-8').write(content)
    print('Fixed analyzer')
else:
    # Find all format strings with None risk
    for i,line in enumerate(content.split('\n')):
        if 'trade.get' in line and ':.2f' in line:
            print(f'Line {i+1}: {line.strip()[:80]}')
