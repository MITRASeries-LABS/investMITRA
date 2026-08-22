import yfinance as yf
stocks = [
    ('HINDCOPPER', 579.30),
    ('CRIZAC', 172.50),
    ('MCX', 3155.40),
    ('ATLANTAELE', 1768.30),
    ('TIMEX', 603.20),
    ('BEL', 413.80),
]
print(f'{"Symbol":<15} {"Entry":>8} {"Close":>8} {"P&L/sh":>8} {"Result"}')
print('-'*55)
for sym, entry in stocks:
    try:
        tk = yf.Ticker(f'{sym}.NS')
        hist = tk.history(period='1d')
        close = float(hist.Close.iloc[-1])
        pnl = close - entry
        result = '?' if pnl > 0 else '?'
        print(f'{sym:<15} {entry:>8.2f} {close:>8.2f} {pnl:>+8.2f}  {result}')
    except Exception as e:
        print(f'{sym:<15} error: {e}')
