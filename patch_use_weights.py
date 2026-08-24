content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# In main() - load weights after preflight and apply to GAP_THRESHOLDS
old = '    # Morning brief to Telegram'

new = '''    # Load Opus-updated weights from Neon
    global GAP_THRESHOLDS
    weights = load_signal_weights()
    if weights:
        GAP_THRESHOLDS["momentum"]  = weights.get("gap_threshold_momentum", 0.3)
        GAP_THRESHOLDS["choppy"]    = weights.get("gap_threshold_choppy", 0.6)
        GAP_THRESHOLDS["afternoon"] = weights.get("gap_threshold_afternoon", 0.4)
        logger.info("Weights loaded: gap_momentum=%.2f sector_rs=%.2f rvol=%.2f",
                    GAP_THRESHOLDS["momentum"],
                    weights.get("sector_rs", 0.12),
                    weights.get("rvol_score", 0.13))
        # Apply skip flags
        if weights.get("skip_choppy_session"):
            SESSIONS.pop("choppy", None)
            logger.info("Choppy session DISABLED by Opus")
        if weights.get("skip_fade_risk"):
            logger.info("fade_risk gaps DISABLED by Opus")

    # Morning brief to Telegram'''

if old in content:
    content = content.replace(old, new)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Weights wired into main()!')
else:
    print('Pattern not found')

# Wire weights into opportunity score calculation
old2 = '''        opp = (
            gap_score     * 0.15 +
            rvol_score    * 0.13 +
            vwap_score    * 0.12 +
            orb_score     * 0.13 +
            holding_score * 0.10 +
            sector_rs     * 0.13 +
            breadth_score * 0.05 +
            regime_score  * 0.05 +
            kl_score      * 0.08 +
            sent_score    * 0.04 +
            bulk_score    * 0.04 +
            preopen_score * 0.03
        ) * sess_mult'''

new2 = '''        # Use Opus-updated weights if available
        try:
            conn2 = psycopg2.connect(NEON_URL, connect_timeout=5)
            cur2  = conn2.cursor()
            cur2.execute("SELECT weights FROM investmitra.signal_weights WHERE effective_date<=CURRENT_DATE ORDER BY effective_date DESC LIMIT 1")
            row2 = cur2.fetchone()
            cur2.close(); conn2.close()
            if row2:
                import json as _json
                w2 = row2[0] if isinstance(row2[0], dict) else _json.loads(row2[0])
            else:
                w2 = {}
        except:
            w2 = {}

        opp = (
            gap_score     * w2.get("gap_score",    0.15) +
            rvol_score    * w2.get("rvol_score",   0.13) +
            vwap_score    * w2.get("vwap_score",   0.12) +
            orb_score     * w2.get("orb_score",    0.13) +
            holding_score * w2.get("holding_score",0.10) +
            sector_rs     * w2.get("sector_rs",    0.13) +
            breadth_score * w2.get("breadth_score",0.05) +
            regime_score  * w2.get("regime_score", 0.05) +
            kl_score      * w2.get("kl_score",     0.08) +
            sent_score    * w2.get("sent_score",   0.04) +
            bulk_score    * w2.get("bulk_score",   0.04) +
            preopen_score * w2.get("preopen_score",0.03)
        ) * sess_mult'''

if old2 in content:
    content = content.replace(old2, new2)
    open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
    print('Opportunity score wired to Opus weights!')
else:
    print('Opp score pattern not found')
