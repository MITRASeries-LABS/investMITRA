import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
conn.autocommit = True
cur = conn.cursor()
cur.execute("""
    CREATE TABLE IF NOT EXISTS investmitra.daily_scores (
        isin              VARCHAR(12),
        score_date        DATE,
        sector            VARCHAR(100),
        price             DECIMAL(15,2),
        investmitra_score DECIMAL(6,2),
        signal            VARCHAR(20),
        momentum_score    DECIMAL(6,2),
        financial_health_score DECIMAL(6,2),
        management_quality_score DECIMAL(6,2),
        financial_stress_score DECIMAL(6,2),
        ret_252d_pct      DECIMAL(10,4),
        vol_20d_pct       DECIMAL(10,4),
        pos_52w           DECIMAL(6,4),
        debt_equity       DECIMAL(10,4),
        pat_margin        DECIMAL(10,4),
        insider_pct       DECIMAL(6,2),
        institution_pct   DECIMAL(6,2),
        ingested_at       TIMESTAMPTZ DEFAULT NOW(),
        PRIMARY KEY (isin, score_date)
    )
""")
print('Table created')
conn.close()
