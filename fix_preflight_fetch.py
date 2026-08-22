content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '    # 2. Neon connection + data freshness'

new = '''    # 1b. Auto-fetch market data if today's data missing
    try:
        cur.execute("SELECT COUNT(*) FROM investmitra.market_indices WHERE fetch_date=CURRENT_DATE")
        if cur.fetchone()[0] == 0:
            print("  ?? Fetching today's market indices...")
            import subprocess
            subprocess.run(["python", "scripts/fetch_market_indices.py"], timeout=30)
            subprocess.run(["python", "scripts/fetch_global_sentiment.py"], timeout=30)
            print("  ? Market data fetched")
    except: pass

    # 2. Neon connection + data freshness'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Fixed!')
else:
    print('Pattern not found')
    for i,line in enumerate(content.split('\n')):
        if 'Neon connection' in line:
            print(f'Line {i+1}: {repr(line)}')
