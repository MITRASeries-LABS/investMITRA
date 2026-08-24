content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = 'GAP_THRESHOLDS = {"momentum": 0.3, "choppy": 0.6, "afternoon": 0.4}'

new = '''GAP_THRESHOLDS = {"momentum": 0.3, "choppy": 0.6, "afternoon": 0.4}  # defaults

def load_signal_weights() -> dict:
    """Load latest signal weights from Neon (updated by weekly Opus review)."""
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        cur  = conn.cursor()
        cur.execute("""
            SELECT weights FROM investmitra.signal_weights
            WHERE effective_date <= CURRENT_DATE
            ORDER BY effective_date DESC LIMIT 1
        """)
        row = cur.fetchone()
        cur.close(); conn.close()
        if row:
            import json
            w = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            logger.info("Loaded signal weights effective %s", w.get("effective_date","?"))
            return w
    except Exception as e:
        logger.warning("Load weights failed: %s ? using defaults", e)
    return {}'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('load_signal_weights added!')
else:
    print('Pattern not found')
