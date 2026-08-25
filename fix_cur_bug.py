content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '''    # 1b. Auto-fetch market data if today's data missing
    try:
        import subprocess
        cur.execute("SELECT COUNT(*) FROM investmitra.market_indices WHERE fetch_date=CURRENT_DATE")'''

new = '''    # 1b. Auto-fetch market data if today's data missing
    try:
        import subprocess
        _conn2 = psycopg2.connect(NEON_URL, connect_timeout=5)
        _cur2  = _conn2.cursor()
        _cur2.execute("SELECT COUNT(*) FROM investmitra.market_indices WHERE fetch_date=CURRENT_DATE")'''

old2 = '''        count = cur.fetchone()[0]'''
new2 = '''        count = _cur2.fetchone()[0]
        _cur2.close(); _conn2.close()'''

if old in content:
    content = content.replace(old, new)
    content = content.replace(old2, new2)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('cur bug fixed!')
else:
    print('Pattern not found')
