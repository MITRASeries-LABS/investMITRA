import yfinance as yf
# GIFT Nifty / Nifty futures tickers
tickers = ['NIFTY50.NS', '^CNXNIFTY', 'NIFTYBEES.NS', 'GN=F', 'NIF=F']
for t in tickers:
    try:
        tk = yf.Ticker(t)
        hist = tk.history(period='1d', interval='1m')
        if not hist.empty:
            last = hist['Close'].iloc[-1]
            print(f'{t}: {last:.2f}')
        else:
            print(f'{t}: No data')
    except Exception as e:
        print(f'{t}: {str(e)[:40]}')
