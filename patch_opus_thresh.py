content = open('scripts/weight_optimizer.py', encoding='utf-8').read()

old = 'def save_new_weights(opus_result: dict, stats: dict):'

new = '''def update_signal_thresholds(stats: dict, trades: list):
    """Self-learning threshold update based on trade performance."""
    try:
        import psycopg2, os
        from datetime import date, timedelta
        conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'), connect_timeout=10)
        conn.autocommit = True
        cur  = conn.cursor()

        cur.execute("SELECT tier1_score_min, tier1_gap_min, tier1_rvol_min, tier2_gap_min, tier2_rvol_min FROM investmitra.signal_thresholds ORDER BY effective_date DESC LIMIT 1")
        row = cur.fetchone()
        if not row:
            cur.close(); conn.close(); return
        t1_score, t1_gap, t1_rvol, t2_gap, t2_rvol = [float(x) for x in row]

        # Tier1 = small gaps (<1%), Tier2 = large gaps (>=1%)
        t1_trades = [t for t in trades if abs(float(t.get('true_gap_pct') or 0)) < 1.0]
        t2_trades = [t for t in trades if abs(float(t.get('true_gap_pct') or 0)) >= 1.0]
        t1_wins = len([t for t in t1_trades if float(t.get('net_pnl',0)) > 0])
        t2_wins = len([t for t in t2_trades if float(t.get('net_pnl',0)) > 0])
        t1_wr = (t1_wins/len(t1_trades)*100) if t1_trades else 0
        t2_wr = (t2_wins/len(t2_trades)*100) if t2_trades else 0

        new_t2_gap   = t2_gap
        new_t1_score = t1_score

        if t2_wr < 35 and len(t2_trades) >= 5:
            new_t2_gap = min(t2_gap + 0.2, 2.5)
        elif t2_wr > 60 and len(t2_trades) >= 5:
            new_t2_gap = max(t2_gap - 0.1, 0.5)

        if t1_wr < 35 and len(t1_trades) >= 10:
            new_t1_score = min(t1_score + 3, 70)
        elif t1_wr > 60 and len(t1_trades) >= 10:
            new_t1_score = max(t1_score - 2, 45)

        next_monday = date.today() + timedelta(days=(7-date.today().weekday()))
        cur.execute("""
            INSERT INTO investmitra.signal_thresholds
                (effective_date, tier1_score_min, tier1_gap_min, tier1_rvol_min,
                 tier2_gap_min, tier2_rvol_min, tier2_traded_min,
                 tier1_win_rate, tier2_win_rate, tier1_trades, tier2_trades,
                 updated_by, notes)
            VALUES (%s,%s,%s,%s,%s,%s,5000000,%s,%s,%s,%s,'weekly_opus',
                    'Auto-adjusted from ' || %s::text || ' trades')
            ON CONFLICT (effective_date) DO UPDATE SET
                tier1_score_min=EXCLUDED.tier1_score_min,
                tier2_gap_min=EXCLUDED.tier2_gap_min,
                tier1_win_rate=EXCLUDED.tier1_win_rate,
                tier2_win_rate=EXCLUDED.tier2_win_rate,
                updated_by=EXCLUDED.updated_by
        """, (next_monday, new_t1_score, t1_gap, t1_rvol,
              new_t2_gap, t2_rvol,
              round(t1_wr,1), round(t2_wr,1),
              len(t1_trades), len(t2_trades), len(trades)))

        cur.close(); conn.close()
        print(f"  Self-learning thresholds updated:")
        print(f"  Tier1: score>{new_t1_score} | win_rate={t1_wr:.1f}%")
        print(f"  Tier2: gap>{new_t2_gap}% | win_rate={t2_wr:.1f}%")
    except Exception as e:
        logger.warning("Threshold update: %s", e)


def save_new_weights(opus_result: dict, stats: dict):'''

if old in content:
    content = content.replace(old, new)
    open('scripts/weight_optimizer.py', 'w', encoding='utf-8').write(content)
    print('Opus threshold updater added!')
else:
    print('Not found')
