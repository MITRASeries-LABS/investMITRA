"""
Patch intraday_signals.py to add Tier 2 momentum universe
and load thresholds from Neon signal_thresholds table
"""

patch_content = r'''
Set-Content patch_tier2.py -Value @"
content = open('scripts/intraday_signals.py', encoding='utf-8').read()

# Add load_thresholds function after load_signal_weights
old_func = 'def get_intraday_watchlist(ctx: dict) -> tuple[list[dict], list[dict]]:'

new_func = '''def load_signal_thresholds() -> dict:
    """Load self-learning signal thresholds from Neon."""
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        cur  = conn.cursor()
        cur.execute("""
            SELECT tier1_score_min, tier1_gap_min, tier1_rvol_min,
                   tier2_gap_min, tier2_rvol_min, tier2_traded_min
            FROM investmitra.signal_thresholds
            WHERE effective_date <= CURRENT_DATE
            ORDER BY effective_date DESC LIMIT 1
        """)
        row = cur.fetchone()
        cur.close(); conn.close()
        if row:
            return {
                'tier1_score_min':  float(row[0]),
                'tier1_gap_min':    float(row[1]),
                'tier1_rvol_min':   float(row[2]),
                'tier2_gap_min':    float(row[3]),
                'tier2_rvol_min':   float(row[4]),
                'tier2_traded_min': int(row[5]),
            }
    except Exception as e:
        logger.warning("Load thresholds: %s", e)
    return {
        'tier1_score_min': 55.0, 'tier1_gap_min': 0.30, 'tier1_rvol_min': 1.5,
        'tier2_gap_min': 1.0, 'tier2_rvol_min': 3.0, 'tier2_traded_min': 5000000,
    }


def get_intraday_watchlist(ctx: dict) -> tuple[list[dict], list[dict]]:'''

if old_func in content:
    content = content.replace(old_func, new_func)
    print('load_signal_thresholds added!')
else:
    print('get_intraday_watchlist not found')

open('scripts/intraday_signals.py', 'w', encoding='utf-8').write(content)
"@ -Encoding ASCII
python patch_tier2.py
'''
print(patch_content)
