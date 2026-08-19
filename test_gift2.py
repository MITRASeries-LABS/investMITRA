import yfinance as yf
# GIFT Nifty / SGX Nifty tickers to try
tickers = ['^NSEI', 'NIFTY.NS', 'NIFTYBEESLP.NS']
for t in tickers:
    try:
        data = yf.Ticker(t)
        info = data.info
        print(f'{t}: {info.get("regularMarketPrice")} ({info.get("regularMarketChangePercent",0):+.2f}%)')
    except Exception as e:
        print(f'{t}: Error - {e}')
