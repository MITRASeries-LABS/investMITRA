import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
conn.autocommit = True
cur = conn.cursor()

entry  = 3209.50
exit_p = 3236.60
qty    = 7
gross  = (exit_p - entry) * qty
net    = gross - 80

cur.execute("""
    INSERT INTO investmitra.trade_log
        (trade_date, symbol, direction, entry_price, exit_price,
         quantity, gross_pnl, net_pnl, outcome, hold_minutes,
         true_gap_pct, gap_type, rvol, sector_rs,
         final_score, market_direction, vix_level, session, atr,
         capital_deployed)
    VALUES ('2026-08-24',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
""", ('MCX','LONG',entry,exit_p,qty,round(gross,2),round(net,2),
      'TIME_EXIT',45,0.47,'continuation',5.1,50,62.2,'NEUTRAL',11.32,
      'momentum',100.20,round(entry*qty,2)))

cur.execute("""
    INSERT INTO investmitra.intraday_pnl
        (trade_date, trades, capital_deployed, gross_pnl, brokerage,
         net_pnl, win_trades, loss_trades, market_direction, vix_level)
    VALUES ('2026-08-24',1,%s,%s,80,%s,1,0,'NEUTRAL',11.32)
    ON CONFLICT (trade_date) DO UPDATE SET
        trades=1, gross_pnl=EXCLUDED.gross_pnl,
        brokerage=80, net_pnl=EXCLUDED.net_pnl,
        win_trades=1, loss_trades=0, saved_at=NOW()
""", (round(entry*qty,2), round(gross,2), round(net,2)))

print(f'MCX saved: entry={entry} exit={exit_p}')
print(f'Gross: Rs{gross:.0f} | Net: Rs{net:.0f}')
conn.close()
