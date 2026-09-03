content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Fix: load thresholds once, not per stock
old = '''        # Load thresholds
        thresholds = load_signal_thresholds()
        t1_score = thresholds.get('tier1_score_min', 55)

        if inv >= t1_score:'''

new = '''        if inv >= long_thresh:'''

if old in content:
    content = content.replace(old, new)
    print('Loop fix applied!')
else:
    print('Pattern not found')

# Load thresholds once at start of get_intraday_watchlist
old2 = '''    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("SELECT UPPER(symbol) FROM investmitra.nse_announcements'''

new2 = '''    # Load thresholds once
    _thresh = load_signal_thresholds()
    long_thresh  = int(_thresh.get('tier1_score_min', 55))

    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("SELECT UPPER(symbol) FROM investmitra.nse_announcements'''

if old2 in content:
    content = content.replace(old2, new2)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Thresholds loaded once at start!')
else:
    print('Pattern 2 not found')
