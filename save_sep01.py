import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
conn.autocommit = True
cur = conn.cursor()

trades = [
    ('APCOTEXIND','LONG',654.40,643.95,38,'REVERSAL',25,0.50,'continuation',7.0,50,57.6),
    ('MANIPALHOS','LONG',773.65,773.40,32,'TIME_EXIT',41,0.42,'continuation',10.0,50,58.6),
    ('MARICO',    'LONG',840.00,836.30,29,'TIME_EXIT',40,0.38,'continuation',6.6,55,61.4),
    ('QPOWER',   'LONG',1455.10,1479.00,17,'TIME_EXIT',45,0.92,'continuation',10.0,50,52.5),
    ('PERSISTENT','LONG',5770.50,5767.00,4,'TIME_EXIT',40,0.48,'continuation',2.7,60,58.0),
    ('CONCORDBIO','LONG',1486.50,1473.70,16,'TIME_EXIT',41,0.89,'continuation',2.0,55,53.5),
    ('SOTL',     'LONG',744.90,771.40,33,'TIME_EXIT',300,0.44,'continuation',26.2,90,59.4),
]

total_net = 0
for sym,direction,entry,exit_p,qty,outcome,hold,gap,gap_type,rvol,sector_rs,score in trades:
    gross = (exit_p - entry) * qty
    net   = gross - 80
    total_net += net
    cur.execute("""
        INSERT INTO investmitra.trade_log
            (trade_date,symbol,direction,entry_price,exit_price,
             quantity,gross_pnl,net_pnl,outcome,hold_minutes,
             true_gap_pct,gap_type,rvol,sector_rs,final_score,
             market_direction,vix_level,session,atr,capital_deployed)
        VALUES ('2026-09-01',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (sym,direction,entry,exit_p,qty,round(gross,2),round(net,2),
          outcome,hold,gap,gap_type,min(rvol,200),sector_rs,score,
          'NEUTRAL',11.0,'momentum',20.0,round(entry*qty,2)))
    print(f'Saved: {sym} net=Rs{net:.0f}')

wins = sum(1 for t in trades if (t[3]-t[2])*t[4] > 80)
cur.execute("""
    INSERT INTO investmitra.intraday_pnl
        (trade_date,trades,capital_deployed,gross_pnl,brokerage,net_pnl,
         win_trades,loss_trades,market_direction,vix_level)
    VALUES ('2026-09-01',%s,%s,%s,%s,%s,%s,%s,'NEUTRAL',11.0)
    ON CONFLICT (trade_date) DO UPDATE SET
        trades=EXCLUDED.trades,gross_pnl=EXCLUDED.gross_pnl,
        brokerage=EXCLUDED.brokerage,net_pnl=EXCLUDED.net_pnl,
        win_trades=EXCLUDED.win_trades,loss_trades=EXCLUDED.loss_trades,
        saved_at=NOW()
""", (7,sum(t[2]*t[4] for t in trades),
      sum((t[3]-t[2])*t[4] for t in trades),
      560,round(total_net,2),wins,7-wins))
print(f'\nNet P&L: Rs{total_net:.0f}')
conn.close()
