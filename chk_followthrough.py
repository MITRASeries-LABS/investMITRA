import yfinance as yf
stocks = [
    ('ANTHEM','LONG',897,0.67),
    ('UNITDSPR','LONG',1534,0.87),
    ('NATIONALUM','SHORT',382,-1.55),
    ('HINDZINC','SHORT',550,-1.43),
    ('HINDCOPPER','SHORT',560,-1.12),
    ('EMMVEE','SHORT',315,-0.80),
    ('HCLTECH','LONG',1309,0.85),
    ('SONACOMS','LONG',829,0.61),
]
print(f'{"Symbol":<13} {"Dir":<6} {"Open":>8} {"High":>8} {"Low":>8} {"Close":>8} {"Move":>8} {"Result"}')
print('-'*80)
for sym, direction, open_p, gap in stocks:
    try:
        tk   = yf.Ticker(f'{sym}.NS')
        hist = tk.history(period='1d', interval='1d')
        if hist.empty: continue
        high  = float(hist.High.iloc[-1])
        low   = float(hist.Low.iloc[-1])
        close = float(hist.Close.iloc[-1])
        if direction == 'LONG':
            move = (high - open_p) / open_p * 100
            result = '? WIN' if move > 1.0 else '?? SMALL' if move > 0.3 else '? FADE'
        else:
            move = (open_p - low) / open_p * 100
            result = '? WIN' if move > 1.0 else '?? SMALL' if move > 0.3 else '? FADE'
        print(f'{sym:<13} {direction:<6} {open_p:>8.0f} {high:>8.2f} {low:>8.2f} {close:>8.2f} {move:>+7.1f}%  {result}')
    except Exception as e:
        print(f'{sym}: error {e}')
