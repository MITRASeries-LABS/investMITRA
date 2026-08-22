import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
conn.autocommit = True
cur = conn.cursor()

# Ensure trade_log table exists
cur.execute("""
    CREATE TABLE IF NOT EXISTS investmitra.trade_log (
        id               SERIAL PRIMARY KEY,
        trade_date       DATE NOT NULL,
        symbol           VARCHAR(20),
        direction        VARCHAR(10),
        entry_price      DECIMAL(12,2),
        exit_price       DECIMAL(12,2),
        quantity         INTEGER,
        gross_pnl        DECIMAL(12,2),
        net_pnl          DECIMAL(12,2),
        outcome          VARCHAR(20),
        hold_minutes     INTEGER,
        true_gap_pct     DECIMAL(8,4),
        gap_type         VARCHAR(30),
        rvol             DECIMAL(8,2),
        sector_rs        DECIMAL(8,2),
        final_score      DECIMAL(8,2),
        market_direction VARCHAR(20),
        vix_level        DECIMAL(8,2),
        session          VARCHAR(20),
        atr              DECIMAL(10,2),
        capital_deployed DECIMAL(12,2),
        created_at       TIMESTAMPTZ DEFAULT NOW()
    )
""")

# Today's paper trades
trades = [
    ('NAM-INDIA','LONG',1254.30,1257.50,19,'TIME_EXIT',45,0.66,'continuation',9.7,50,63.9,'NEUTRAL',11.32,'momentum',30.45),
    ('EMMVEE',   'LONG',331.30, 329.90, 75,'TIME_EXIT',45,3.14,'continuation',19.1,73,73.9,'NEUTRAL',11.32,'momentum',8.92),
    ('MCX',      'LONG',3071.00,3097.00,8, 'TIME_EXIT',45,1.58,'continuation',3.7, 65,71.4,'NEUTRAL',11.32,'momentum',112.81),
]

for t in trades:
    sym,direction,entry,exit_p,qty,outcome,hold,gap,gap_type,rvol,sector_rs,score,mkt_dir,vix,session,atr = t
    gross = (exit_p - entry) * qty
    net   = gross - 80
    cur.execute("""
        INSERT INTO investmitra.trade_log
            (trade_date, symbol, direction, entry_price, exit_price,
             quantity, gross_pnl, net_pnl, outcome, hold_minutes,
             true_gap_pct, gap_type, rvol, sector_rs,
             final_score, market_direction, vix_level, session, atr,
             capital_deployed)
        VALUES (CURRENT_DATE,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (sym,direction,entry,exit_p,qty,round(gross,2),round(net,2),
          outcome,hold,gap,gap_type,rvol,sector_rs,score,mkt_dir,vix,session,atr,
          round(entry*qty,2)))
    print(f'Saved: {sym} {direction} gross=₹{gross:.0f} net=₹{net:.0f}')

# Update intraday_pnl
cur.execute("""
    INSERT INTO investmitra.intraday_pnl
        (trade_date, trades, capital_deployed, gross_pnl, brokerage,
         net_pnl, win_trades, loss_trades, market_direction, vix_level)
    VALUES (CURRENT_DATE, 3, 73250, 164, 240, -76, 2, 1, 'NEUTRAL', 11.32)
    ON CONFLICT (trade_date) DO UPDATE SET
        trades=3, gross_pnl=164, brokerage=240, net_pnl=-76,
        win_trades=2, loss_trades=1, saved_at=NOW()
""")
print('intraday_pnl updated — Net P&L: -76')
conn.close()
