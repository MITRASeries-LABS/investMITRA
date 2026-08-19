import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
conn.autocommit = True
cur = conn.cursor()
cur.execute('''
    CREATE TABLE IF NOT EXISTS investmitra.intraday_pnl (
        id               SERIAL PRIMARY KEY,
        trade_date       DATE NOT NULL UNIQUE,
        trades           INTEGER DEFAULT 0,
        capital_deployed DECIMAL(12,2) DEFAULT 0,
        gross_pnl        DECIMAL(12,2) DEFAULT 0,
        brokerage        DECIMAL(12,2) DEFAULT 0,
        net_pnl          DECIMAL(12,2) DEFAULT 0,
        win_trades       INTEGER DEFAULT 0,
        loss_trades      INTEGER DEFAULT 0,
        market_direction VARCHAR(20),
        vix_level        DECIMAL(8,2),
        signals          JSONB,
        saved_at         TIMESTAMPTZ DEFAULT NOW()
    )
''')
print('Table created successfully')

# Insert today as placeholder so panel shows
cur.execute("""
    INSERT INTO investmitra.intraday_pnl
        (trade_date, trades, capital_deployed, gross_pnl, brokerage,
         net_pnl, win_trades, loss_trades, market_direction, vix_level)
    VALUES (CURRENT_DATE, 3, 49000, 18, 240, -222, 0, 3, 'NEUTRAL', 11.39)
    ON CONFLICT (trade_date) DO NOTHING
""")
print('Today paper trade data inserted')
conn.close()
