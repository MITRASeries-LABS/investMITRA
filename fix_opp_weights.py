content = open('scripts/intraday_signals.py', encoding='utf-8').read()

old = '''        opp = (
            gap_score     * 0.15 +
            rvol_score    * 0.12 +
            vwap_score    * 0.10 +
            orb_score     * 0.12 +
            holding_score * 0.10 +
            sector_rs     * 0.12 +
            breadth_score * 0.05 +
            regime_score  * 0.05 +
            kl_score      * 0.08 +
            sent_score    * 0.04 +
            bulk_score    * 0.04 +
            preopen_score * 0.03
        ) * sess_mult'''

new = '''        # Load Opus weights
        try:
            import json as _j
            _conn = psycopg2.connect(NEON_URL, connect_timeout=5)
            _cur  = _conn.cursor()
            _cur.execute("SELECT weights FROM investmitra.signal_weights WHERE effective_date<=CURRENT_DATE ORDER BY effective_date DESC LIMIT 1")
            _row  = _cur.fetchone()
            _cur.close(); _conn.close()
            _w = _row[0] if isinstance(_row[0], dict) else _j.loads(_row[0]) if _row else {}
        except:
            _w = {}

        opp = (
            gap_score     * _w.get("gap_score",     0.15) +
            rvol_score    * _w.get("rvol_score",    0.12) +
            vwap_score    * _w.get("vwap_score",    0.10) +
            orb_score     * _w.get("orb_score",     0.12) +
            holding_score * _w.get("holding_score", 0.10) +
            sector_rs     * _w.get("sector_rs",     0.12) +
            breadth_score * _w.get("breadth_score", 0.05) +
            regime_score  * _w.get("regime_score",  0.05) +
            kl_score      * _w.get("kl_score",      0.08) +
            sent_score    * _w.get("sent_score",     0.04) +
            bulk_score    * _w.get("bulk_score",     0.04) +
            preopen_score * _w.get("preopen_score",  0.03)
        ) * sess_mult'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Opp score wired to Opus weights!')
else:
    print('Not found - checking whitespace')
    idx = content.find('        opp = (')
    print(repr(content[idx:idx+200]))
