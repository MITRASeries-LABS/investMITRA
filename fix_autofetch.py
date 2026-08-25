content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '    # 1b. Auto-fetch market data if today\'s data missing'

new = '    # 1b. Auto-fetch market data if today\'s data missing (always run at startup)'

# Find the auto-fetch block and make it always run, not conditional
old2 = '''    # 1b. Auto-fetch market data if today's data missing
    try:
        cur.execute("SELECT COUNT(*) FROM investmitra.market_indices WHERE fetch_date=CURRENT_DATE")
        if cur.fetchone()[0] == 0:
            print("  ?? Fetching today's market indices...")
            import subprocess
            subprocess.run(["python", "scripts/fetch_market_indices.py"], timeout=30)
            subprocess.run(["python", "scripts/fetch_global_sentiment.py"], timeout=30)
            print("  ? Market data fetched")
    except: pass'''

new2 = '''    # 1b. Auto-fetch market data if today's data missing
    try:
        import subprocess
        cur.execute("SELECT COUNT(*) FROM investmitra.market_indices WHERE fetch_date=CURRENT_DATE")
        count = cur.fetchone()[0]
        if count == 0:
            print("  Fetching today's market indices...")
            subprocess.run(["python", "scripts/fetch_market_indices.py"], timeout=60, cwd=os.getcwd())
            subprocess.run(["python", "scripts/fetch_global_sentiment.py"], timeout=60, cwd=os.getcwd())
            print("  Market data fetched")
        else:
            print(f"  Market indices: {count} records today")
    except Exception as e:
        print(f"  Auto-fetch failed: {e}")
        try:
            subprocess.run(["python", "scripts/fetch_market_indices.py"], timeout=60)
            subprocess.run(["python", "scripts/fetch_global_sentiment.py"], timeout=60)
        except: pass'''

if old2 in content:
    content = content.replace(old2, new2)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Fixed!')
else:
    print('Pattern not found')
