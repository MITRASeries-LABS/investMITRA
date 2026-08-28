"""
Patch weight_optimizer.py to update signal_thresholds after weekly review
"""
patch = r'''
Set-Content patch_opus_thresh.py -Value @"
content = open('scripts/weight_optimizer.py', encoding='utf-8').read()

old = "def save_new_weights(opus_result: dict, stats: dict):"

new = '''def update_signal_thresholds(stats: dict, trades: list):
    """
    Self-learning threshold update based on trade performance.
    Runs after weekly Opus review.
    """
    try:
        import psycopg2, json
        from dotenv import load_dotenv
        load_dotenv('.env.prod')
        conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'), connect_timeout=10)
        conn.autocommit = True
        cur  = conn.cursor()

        # Get current thresholds
        cur.execute("SELECT tier1_score_min, tier1_gap_min, tier1_rvol_min, tier2_gap_min, tier2_rvol_min FROM investmitra.signal_thresholds ORDER BY effective_date DESC LIMIT 1")
        row = cur.fetchone()
        if not row: return
        t1_score, t1_gap, t1_rvol, t2_gap, t2_rvol = [float(x) for x in row]

        # Analyze tier1 vs tier2 performance
        # Tier2: trades with gap > 1% (momentum plays like ATHERENERG)
        t1_trades = [t for t in trades if abs(float(t.get('true_gap_pct',0))) < 1.0]
        t2_trades = [t for t in trades if abs(float(t.get('true_gap_pct',0))) >= 1.0]

        t1_wins = len([t for t in t1_trades if float(t.get('net_pnl',0)) > 0])
        t2_wins = len([t for t in t2_trades if float(t.get('net_pnl',0)) > 0])

        t1_wr = (t1_wins/len(t1_trades)*100) if t1_trades else 0
        t2_wr = (t2_wins/len(t2_trades)*100) if t2_trades else 0

        # Self-adjust thresholds
        new_t2_gap = t2_gap
        new_t1_score = t1_score

        if t2_wr < 35 and len(t2_trades) >= 5:
            new_t2_gap = min(t2_gap + 0.1, 2.0)  # Raise tier2 gap threshold
            logger.info("Tier2 win rate %.1f%% low - raising gap to %.2f%%", t2_wr, new_t2_gap)
        elif t2_wr > 60 and len(t2_trades) >= 5:
            new_t2_gap = max(t2_gap - 0.1, 0.5)  # Lower tier2 gap threshold
            logger.info("Tier2 win rate %.1f%% high - lowering gap to %.2f%%", t2_wr, new_t2_gap)

        if t1_wr < 35 and len(t1_trades) >= 10:
            new_t1_score = min(t1_score + 2, 70)  # Raise score requirement
        elif t1_wr > 60 and len(t1_trades) >= 10:
            new_t1_score = max(t1_score - 2, 45)  # Lower score requirement

        from datetime import date, timedelta
        next_monday = date.today() + timedelta(days=(7-date.today().weekday()))

        cur.execute("""
            INSERT INTO investmitra.signal_thresholds
                (effective_date, tier1_score_min, tier1_gap_min, tier1_rvol_min,
                 tier2_gap_min, tier2_rvol_min, tier2_traded_min,
                 tier1_win_rate, tier2_win_rate, tier1_trades, tier2_trades,
                 updated_by, notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'weekly_opus',
                    'Self-adjusted based on ' || %s::text || ' trades')
            ON CONFLICT (effective_date) DO UPDATE SET
                tier1_score_min=EXCLUDED.tier1_score_min,
                tier2_gap_min=EXCLUDED.tier2_gap_min,
                tier1_win_rate=EXCLUDED.tier1_win_rate,
                tier2_win_rate=EXCLUDED.tier2_win_rate,
                updated_by=EXCLUDED.updated_by
        """, (next_monday, new_t1_score, t1_gap, t1_rvol,
              new_t2_gap, t2_rvol, 5000000,
              round(t1_wr,1), round(t2_wr,1),
              len(t1_trades), len(t2_trades), len(trades)))

        cur.close(); conn.close()
        print(f"  Thresholds updated: T1 score>{new_t1_score} gap>{t1_gap}")
        print(f"  Tier2: gap>{new_t2_gap}% rvol>{t2_rvol}x")
        print(f"  Win rates: T1={t1_wr:.1f}% T2={t2_wr:.1f}%")

    except Exception as e:
        logger.warning("Threshold update: %s", e)


def save_new_weights(opus_result: dict, stats: dict):'''

if old in content:
    content = content.replace(old, new)
    open('scripts/weight_optimizer.py', 'w', encoding='utf-8').write(content)
    print('Opus threshold updater added!')
else:
    print('Pattern not found')
"@ -Encoding ASCII
python patch_opus_thresh.py
'''
print(patch)
