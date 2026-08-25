import yfinance as yf
stocks = ['MCX','HAL','BEL','OFSS','HINDCOPPER','EMMVEE','ATLANTAELE']
print(f'{"Symbol":<15} {"Open":>8} {"Close":>8} {"Gap%":>8} {"Move%":>8}')
print('-'*55)
for sym in stocks:
    try:
        tk = yf.Ticker(f'{sym}.NS')
        hist = tk.history(period='2d', interval='1d')
        if len(hist) < 2: continue
        prev_close = float(hist.Close.iloc[-2])
        today_open = float(hist.Open.iloc[-1])
        today_close = float(hist.Close.iloc[-1])
        gap = (today_open - prev_close) / prev_close * 100
        move = (today_close - today_open) / today_open * 100
        print(f'{sym:<15} {today_open:>8.2f} {today_close:>8.2f} {gap:>+8.2f}% {move:>+8.2f}%')
    except Exception as e:
        print(f'{sym}: {e}')
