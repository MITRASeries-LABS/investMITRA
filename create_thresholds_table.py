import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')

conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
conn.autocommit = True
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS investmitra.signal_thresholds (
    id                  SERIAL PRIMARY KEY,
    effective_date      DATE NOT NULL UNIQUE,
    tier1_score_min     DECIMAL(5,2) DEFAULT 55.0,
    tier1_gap_min       DECIMAL(5,3) DEFAULT 0.30,
    tier1_rvol_min      DECIMAL(5,2) DEFAULT 1.5,
    tier2_gap_min       DECIMAL(5,3) DEFAULT 1.0,
    tier2_rvol_min      DECIMAL(5,2) DEFAULT 3.0,
    tier2_traded_min    BIGINT DEFAULT 5000000,
    tier1_win_rate      DECIMAL(5,2),
    tier2_win_rate      DECIMAL(5,2),
    tier1_trades        INTEGER DEFAULT 0,
    tier2_trades        INTEGER DEFAULT 0,
    updated_by          VARCHAR(50) DEFAULT 'default',
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
)
""")

cur.execute("""
INSERT INTO investmitra.signal_thresholds 
    (effective_date, tier1_score_min, tier1_gap_min, tier1_rvol_min,
     tier2_gap_min, tier2_rvol_min, tier2_traded_min, updated_by, notes)
VALUES 
    (CURRENT_DATE, 55.0, 0.30, 1.5, 1.0, 3.0, 5000000, 'default',
     'Initial - Tier1=quality stocks score>55, Tier2=momentum gap>1% RVOL>3x')
ON CONFLICT (effective_date) DO NOTHING
""")

print('signal_thresholds table created!')
cur.execute("SELECT * FROM investmitra.signal_thresholds ORDER BY effective_date DESC LIMIT 1")
row = cur.fetchone()
print(f'Tier1: score>{row[2]} gap>{row[3]} rvol>{row[4]}')
print(f'Tier2: gap>{row[5]} rvol>{row[6]} traded>{row[7]:,}')
conn.close()
