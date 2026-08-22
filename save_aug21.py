import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
conn.autocommit = True
cur = conn.cursor()

trades = [
    ('HINDCOPPER','LONG',579.30,572.70,43,'TIME_EXIT',45,0.51,'fade_risk',13.7,50,61.8,'NEUTRAL',0,'momentum',27.65),
    ('CRIZAC','LONG',172.50,172.84,144,'TIME_EXIT',45,0.76,'fade_risk',1.6,50,51.9,'NEUTRAL',0,'momentum',5.88),
    ('MCX','LONG',3155.40,3185.00,7,'TIME_EXIT',45,0.61,'fade_risk',5.3,65,66.2,'NEUTRAL',0,'momentum',105.38),
    ('ATLANTAELE','LONG',1768.30,1846.90,13,'TIME_EXIT',45,1.44,'continuation',3.8,60,57.5,'NEUTRAL',0,'momentum',97.35),
    ('TIMEX','LONG',603.20,599.95,41,'TIME_EXIT',45,0.60,'continuation',2.3,50,52.5,'NEUTRAL',0,'momentum',24.86),
    ('BEL','LONG',413.80,414.00,60,'TIME_EXIT',45,0.99,'continuation',1.9,58,57.9,'NEUTRAL',0,'momentum',6.10),
]

total_net = 0
for t in trades:
    sym,direction,entry,exit_p,qty,outcome,hold,gap,gap_type,rvol,sector_rs,score,mkt_dir,vix,session,atr = t
    gross = (exit_p - entry) * qty
    net   = gross - 80
    total_net += net
    cur.execute("""
        INSERT INTO investmitra.trade_log
            (trade_date, symbol, direction, entry_price, exit_price,
             quantity, gross_pnl, net_pnl, outcome, hold_minutes,
             true_gap_pct, gap_type, rvol, sector_rs,
             final_score, market_direction, vix_level, session, atr,
             capital_deployed)
        VALUES ('2026-08-21',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (sym,direction,entry,exit_p,qty,round(gross,2),round(net,2),
          outcome,hold,gap,gap_type,rvol,sector_rs,score,mkt_dir,vix,session,atr,
          round(entry*qty,2)))
    print(f'Saved: {sym} gross=?{gross:.0f} net=?{net:.0f}')

wins = sum(1 for t in trades if (t[3]-t[2])*t[4] > 0)
cur.execute("""
    INSERT INTO investmitra.intraday_pnl
        (trade_date, trades, capital_deployed, gross_pnl, brokerage,
         net_pnl, win_trades, loss_trades, market_direction, vix_level)
    VALUES ('2026-08-21',%s,%s,%s,%s,%s,%s,%s,'NEUTRAL',0)
    ON CONFLICT (trade_date) DO UPDATE SET
        trades=EXCLUDED.trades, gross_pnl=EXCLUDED.gross_pnl,
        brokerage=EXCLUDED.brokerage, net_pnl=EXCLUDED.net_pnl,
        win_trades=EXCLUDED.win_trades, loss_trades=EXCLUDED.loss_trades,
        saved_at=NOW()
""", (6, sum(t[2]*t[4] for t in trades), 
      sum((t[3]-t[2])*t[4] for t in trades),
      480, round(total_net,2), wins, 6-wins))
print(f'intraday_pnl saved ? Net: ?{total_net:.0f}')
conn.close()
