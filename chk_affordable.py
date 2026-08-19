import yfinance as yf
import psycopg2, os
from dotenv import load_dotenv
load_dotenv('.env.prod')
conn = psycopg2.connect(os.getenv('CC_POSTGRES_URL'))
cur = conn.cursor()
cur.execute("""
    SELECT cm.nse_symbol, cm.market_cap_category, ds.investmitra_score,
           ROUND(AVG(ep.volume)::numeric,0) as avg_vol,
           ROUND(AVG(ep.close)::numeric,2) as avg_price,
           ROUND(AVG(ep.volume)*AVG(ep.close)::numeric,0) as avg_traded
    FROM investmitra.daily_scores ds
    JOIN investmitra.company_master cm ON ds.isin=cm.isin
    JOIN investmitra.equity_prices ep ON ds.isin=ep.isin
    WHERE ds.score_date=(SELECT MAX(score_date) FROM investmitra.daily_scores)
      AND ds.investmitra_score >= 55
      AND cm.market_cap_category IN ('MID','LARGE','SMALL')
      AND ep.trade_date >= CURRENT_DATE - INTERVAL '30 days'
    GROUP BY cm.nse_symbol, cm.market_cap_category, ds.investmitra_score
    HAVING AVG(ep.close) BETWEEN 200 AND 2000
       AND AVG(ep.volume)*AVG(ep.close) >= 50000000
    ORDER BY ds.investmitra_score DESC
    LIMIT 40
""")
symbols_data = {r[0]: r for r in cur.fetchall()}
conn.close()

print(f'Checking {len(symbols_data)} affordable stocks (?200-?2000) for true gaps today...')
print(f'{"Symbol":<13} {"Cap":<7} {"Score":>6} {"Price":>8} {"True Gap":>9} {"Signal?"}')
print('-'*60)

signals = []
for sym, data in symbols_data.items():
    try:
        tk   = yf.Ticker(f'{sym}.NS')
        hist = tk.history(period='2d', interval='1d')
        if len(hist) < 2: continue
        prev_close = float(hist['Close'].iloc[-2])
        today_open = float(hist['Open'].iloc[-1])
        true_gap   = (today_open - prev_close) / prev_close * 100
        signal = '?? LONG' if true_gap > 0.3 else '?? SHORT' if true_gap < -0.3 else '?'
        print(f'{sym:<13} {data[1]:<7} {float(data[2]):>6.1f} {prev_close:>8.2f} {true_gap:>+8.2f}%  {signal}')
        if abs(true_gap) > 0.3:
            signals.append((sym, data[1], float(data[2]), prev_close, today_open, true_gap))
    except: pass

print(f'\n{"="*60}')
print(f'Genuine gaps >0.3% in ?200-?2000 range: {len(signals)}')
for s in sorted(signals, key=lambda x: abs(x[5]), reverse=True):
    size_by_capital = int(25000 / s[4])
    atr_est = s[3] * 0.015  # rough 1.5% ATR estimate
    size_by_risk = int(2000 / (atr_est * 1.5))
    size = min(size_by_capital, size_by_risk)
    capital = size * s[4]
    print(f'  {s[0]:<13} [{s[1]}] Score:{s[2]:.0f} Price:?{s[4]:.0f} Gap:{s[5]:+.2f}% ? {size} shares ?{capital:,.0f}')
