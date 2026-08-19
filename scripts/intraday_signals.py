"""
investMITRA — Intraday Signal Engine v5
Full pre-market sentiment: Nifty Futures + India VIX + Global Markets

Pre-market analysis (before 9:15 AM):
  1. Nifty Futures (Kite) — primary direction
  2. India VIX (Neon)     — fear level filter
  3. SGX Nifty (Neon)     — confirmation
  4. US/Global (Neon)     — global context

Combined signal:
  BULLISH → LONG only
  BEARISH → SHORT only
  NEUTRAL → both
  HIGH VIX (>20) → reduce size or avoid

Run: python scripts/intraday_signals.py
"""
from __future__ import annotations
import os, sys, time, logging
from datetime import datetime, date, timedelta, timezone
from collections import defaultdict
import psycopg2
from dotenv import load_dotenv
load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

from kiteconnect import KiteConnect, KiteTicker

API_KEY      = os.getenv("KITE_API_KEY")
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN")
NEON_URL     = os.getenv("CC_POSTGRES_URL")
IST          = timezone(timedelta(hours=5, minutes=30))


def get_premarket_sentiment() -> dict:
    """
    Read pre-market data from Neon (saved by overnight pipeline).
    Returns combined sentiment dict.
    """
    result = {
        "india_vix":      None,
        "sgx_nifty_chg":  None,
        "us_sentiment":   "UNKNOWN",
        "vix_signal":     "NORMAL",  # NORMAL, ELEVATED, HIGH
        "global_signal":  "NEUTRAL",
    }

    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        cur  = conn.cursor()

        # India VIX
        cur.execute("""
            SELECT last_price, change_pct FROM investmitra.market_indices
            WHERE index_name = 'INDIA VIX' AND fetch_date = CURRENT_DATE
            ORDER BY fetched_at DESC LIMIT 1
        """)
        vix_row = cur.fetchone()
        if vix_row:
            result["india_vix"] = float(vix_row[0])

        # SGX Nifty from global indices
        cur.execute("""
            SELECT last_price, change_str FROM investmitra.global_indices
            WHERE index_name ILIKE '%SGX%NIFTY%' AND fetch_date = CURRENT_DATE
            ORDER BY fetched_at DESC LIMIT 1
        """)
        sgx_row = cur.fetchone()
        if sgx_row:
            try:
                chg = str(sgx_row[1]).replace('+','').replace('%','').strip()
                result["sgx_nifty_chg"] = float(chg)
            except: pass

        # US markets sentiment
        cur.execute("""
            SELECT index_name, last_price, change_str FROM investmitra.global_indices
            WHERE index_name ILIKE '%DOW%' AND fetch_date = CURRENT_DATE
            ORDER BY fetched_at DESC LIMIT 1
        """)
        dow_row = cur.fetchone()
        if dow_row:
            try:
                chg = str(dow_row[2]).replace('+','').replace(',','').strip()
                dow_chg = float(chg)
                if dow_chg > 0:
                    result["us_sentiment"] = "POSITIVE"
                elif dow_chg < -200:
                    result["us_sentiment"] = "NEGATIVE"
                else:
                    result["us_sentiment"] = "MIXED"
            except: pass

        cur.close(); conn.close()

    except Exception as e:
        logger.warning("Pre-market sentiment fetch failed: %s", e)

    # VIX signal
    vix = result["india_vix"]
    if vix:
        if vix < 12:
            result["vix_signal"] = "CALM"
        elif vix < 16:
            result["vix_signal"] = "NORMAL"
        elif vix < 20:
            result["vix_signal"] = "ELEVATED"
        else:
            result["vix_signal"] = "HIGH"

    # Global signal
    sgx = result["sgx_nifty_chg"]
    us  = result["us_sentiment"]
    if sgx and sgx > 0.2 and us != "NEGATIVE":
        result["global_signal"] = "BULLISH"
    elif sgx and sgx < -0.2 and us == "NEGATIVE":
        result["global_signal"] = "BEARISH"
    else:
        result["global_signal"] = "NEUTRAL"

    return result


def get_market_direction(kite: KiteConnect) -> tuple[str, float]:
    """
    Fetch Nifty Futures for primary direction.
    Combines with pre-market sentiment for final signal.
    """
    # Get pre-market sentiment from Neon
    sentiment = get_premarket_sentiment()

    # Print pre-market context
    print(f"\n{'='*58}")
    print(f"  PRE-MARKET SENTIMENT — {date.today()}")
    print(f"{'='*58}")

    vix = sentiment["india_vix"]
    sgx = sentiment["sgx_nifty_chg"]

    if vix:
        vix_emoji = "🟢" if vix < 12 else "🟡" if vix < 16 else "🔴"
        print(f"  {vix_emoji} India VIX:    {vix:.2f} ({sentiment['vix_signal']})")
    if sgx is not None:
        sgx_emoji = "🟢" if sgx > 0 else "🔴"
        print(f"  {sgx_emoji} SGX Nifty:   {sgx:+.2f}%")

    us_emoji = "🟢" if sentiment["us_sentiment"] == "POSITIVE" else "🔴" if sentiment["us_sentiment"] == "NEGATIVE" else "🟡"
    print(f"  {us_emoji} US Markets:  {sentiment['us_sentiment']}")
    print(f"  Global:      {sentiment['global_signal']}")

    # High VIX warning
    if sentiment["vix_signal"] == "HIGH":
        print(f"\n  ⚠️  HIGH VIX — Reduce position size or avoid intraday!")

    print(f"{'─'*58}")

    # Get Nifty Futures from Kite
    fut_price  = 0
    prev_close = 0
    source     = "Nifty 50 Spot"

    try:
        instruments = kite.instruments("NFO")
        nifty_futs  = [i for i in instruments
                       if i["name"] == "NIFTY" and i["instrument_type"] == "FUT"]
        nifty_futs.sort(key=lambda x: x["expiry"])

        if nifty_futs:
            near_fut   = nifty_futs[0]
            fut_symbol = f"NFO:{near_fut['tradingsymbol']}"
            quote      = kite.quote([fut_symbol])
            fut_data   = quote.get(fut_symbol, {})
            prev_close = fut_data.get("ohlc", {}).get("close", 0)
            fut_price  = fut_data.get("last_price", 0)
            source     = f"Nifty Fut ({near_fut['tradingsymbol']})"
    except Exception as e:
        logger.warning("Futures failed (%s) — using spot", e)
        try:
            quote      = kite.quote(["NSE:NIFTY 50"])
            spot       = quote.get("NSE:NIFTY 50", {})
            prev_close = spot.get("ohlc", {}).get("close", 0)
            fut_price  = spot.get("last_price", 0)
        except: pass

    change_pct = (fut_price - prev_close) / prev_close * 100 if prev_close else 0

    # Primary direction from futures
    if change_pct > 0.3:
        fut_direction = "BULLISH"
    elif change_pct < -0.3:
        fut_direction = "BEARISH"
    else:
        fut_direction = "NEUTRAL"

    # Combine futures + global signal
    if fut_direction == "BULLISH" and sentiment["global_signal"] != "BEARISH":
        direction = "BULLISH"
        emoji     = "🟢"
        advice    = "Taking LONG signals only"
    elif fut_direction == "BEARISH" and sentiment["global_signal"] != "BULLISH":
        direction = "BEARISH"
        emoji     = "🔴"
        advice    = "Taking SHORT signals only"
    elif fut_direction == "BULLISH" and sentiment["global_signal"] == "BEARISH":
        direction = "NEUTRAL"
        emoji     = "🟡"
        advice    = "Mixed signals — selective LONG only with tight SL"
    elif fut_direction == "BEARISH" and sentiment["global_signal"] == "BULLISH":
        direction = "NEUTRAL"
        emoji     = "🟡"
        advice    = "Mixed signals — selective SHORT only with tight SL"
    else:
        direction = "NEUTRAL"
        emoji     = "🟡"
        advice    = "Taking both LONG and SHORT signals"

    # Override if VIX is HIGH
    if sentiment["vix_signal"] == "HIGH":
        advice = f"{advice} | REDUCE SIZE (High VIX)"

    print(f"  {emoji} NIFTY FUTURES: {change_pct:+.2f}% ({source})")
    print(f"  ₹{fut_price:,.2f}  (prev: ₹{prev_close:,.2f})")
    print(f"{'─'*58}")
    print(f"  FINAL DIRECTION: {direction}")
    print(f"  Strategy: {advice}")
    print(f"{'='*58}\n")

    return direction, change_pct


def get_intraday_watchlist() -> tuple[list[dict], list[dict]]:
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("""
        WITH avg_volume AS (
            SELECT isin, AVG(volume) AS avg_vol, AVG(close) AS avg_price
            FROM investmitra.equity_prices
            WHERE trade_date >= CURRENT_DATE - INTERVAL '30 days'
            GROUP BY isin
            HAVING AVG(volume) > 200000 AND AVG(close) BETWEEN 50 AND 5000
        )
        SELECT ds.isin, ds.company_name, cm.nse_symbol, ds.sector,
               cm.market_cap_category, ds.investmitra_score, ds.signal,
               ds.momentum_score,
               ROUND(av.avg_vol::numeric, 0), ROUND(av.avg_price::numeric, 2),
               COALESCE(ss.screen_count, 0)
        FROM investmitra.daily_scores ds
        JOIN investmitra.company_master cm ON ds.isin = cm.isin
        JOIN avg_volume av ON ds.isin = av.isin
        LEFT JOIN (
            SELECT isin, COUNT(DISTINCT screen_name) AS screen_count
            FROM investmitra.screener_signals
            WHERE signal_date = (SELECT MAX(signal_date) FROM investmitra.screener_signals)
            GROUP BY isin
        ) ss ON ds.isin = ss.isin
        WHERE ds.score_date = (SELECT MAX(score_date) FROM investmitra.daily_scores)
          AND cm.nse_symbol IS NOT NULL
          AND cm.market_cap_category IN ('MID', 'LARGE')
        ORDER BY ds.investmitra_score DESC
    """)
    rows = cur.fetchall()
    cur.close(); conn.close()

    long_list = []
    short_list = []
    for r in rows:
        stock = {
            "isin": r[0], "company_name": r[1], "symbol": r[2],
            "sector": r[3], "cap": r[4],
            "investmitra_score": float(r[5] or 0), "signal": r[6],
            "momentum_score": float(r[7] or 0),
            "avg_volume": int(r[8] or 0), "avg_price": float(r[9] or 0),
            "screen_count": int(r[10] or 0),
        }
        if stock["investmitra_score"] >= 60:
            long_list.append(stock)
        elif stock["investmitra_score"] <= 35:
            short_list.append(stock)

    long_list  = sorted(long_list, key=lambda x: x["investmitra_score"]*0.6+x["screen_count"]*2, reverse=True)[:15]
    short_list = sorted(short_list, key=lambda x: x["investmitra_score"])[:10]
    return long_list, short_list


def get_instrument_tokens(kite, symbols):
    try:
        instruments = kite.instruments("NSE")
        return {i["tradingsymbol"]: i["instrument_token"]
                for i in instruments if i["tradingsymbol"] in symbols and i["segment"] == "NSE"}
    except Exception as e:
        logger.error("Instruments failed: %s", e)
        return {}


def get_previous_close(kite, symbols):
    try:
        quotes = kite.quote([f"NSE:{s}" for s in symbols])
        return {s.replace("NSE:", ""): d["ohlc"]["close"] for s, d in quotes.items()}
    except Exception as e:
        logger.error("Quote failed: %s", e)
        return {}


class IntradayEngine:
    def __init__(self, long_list, short_list, token_map, prev_close, market_direction):
        self.long_map         = {s["symbol"]: s for s in long_list}
        self.short_map        = {s["symbol"]: s for s in short_list}
        self.all_stocks       = {**self.long_map, **self.short_map}
        self.token_map        = token_map
        self.rev_tokens       = {v: k for k, v in token_map.items()}
        self.prev_close       = prev_close
        self.market_direction = market_direction
        self.ticks            = defaultdict(list)
        self.vwap             = defaultdict(float)
        self.cum_vol          = defaultdict(int)
        self.cum_tp_vol       = defaultdict(float)
        self.first_tick       = defaultdict(bool)
        self.signals          = {}

    def on_tick(self, ws, ticks):
        now = datetime.now(IST)
        for tick in ticks:
            token  = tick["instrument_token"]
            symbol = self.rev_tokens.get(token)
            if not symbol or symbol not in self.all_stocks: continue
            ltp    = tick.get("last_price", 0)
            volume = tick.get("volume_traded", 0)
            if ltp <= 0: continue
            if not self.first_tick[symbol]:
                self.first_tick[symbol] = True
            new_vol = max(0, volume - self.cum_vol[symbol])
            if new_vol > 0:
                self.cum_vol[symbol]    = volume
                self.cum_tp_vol[symbol] += ltp * new_vol
                if volume > 0:
                    self.vwap[symbol] = self.cum_tp_vol[symbol] / volume
            if now.hour * 60 + now.minute >= 9 * 60 + 30:
                self._check_signal(symbol, ltp, volume, now)

    def _check_signal(self, symbol, ltp, volume, now):
        if symbol in self.signals: return
        prev   = self.prev_close.get(symbol, 0)
        vwap   = self.vwap.get(symbol, ltp)
        stock  = self.all_stocks.get(symbol, {})
        score  = stock.get("investmitra_score", 50)
        avg_vol= stock.get("avg_volume", 0)
        if not prev: return
        gap_pct    = (ltp - prev) / prev * 100
        above_vwap = ltp > vwap * 1.001
        below_vwap = ltp < vwap * 0.999
        mkt_min    = now.hour * 60 + now.minute - (9 * 60 + 15)
        frac_day   = max(mkt_min / 375, 0.1)
        vol_surge  = volume > avg_vol * frac_day * 1.5 if avg_vol > 0 else False
        can_long   = self.market_direction in ("BULLISH", "NEUTRAL")
        can_short  = self.market_direction in ("BEARISH", "NEUTRAL")

        if symbol in self.long_map and can_long and gap_pct > 0.3 and above_vwap and score >= 60:
            self._emit_signal(symbol, "LONG", ltp, round(ltp*1.015,2), round(ltp*0.995,2),
                              gap_pct, vwap, score, stock, now, vol_surge)
        elif symbol in self.short_map and can_short and gap_pct < -0.3 and below_vwap and score <= 40:
            self._emit_signal(symbol, "SHORT", ltp, round(ltp*0.985,2), round(ltp*1.005,2),
                              gap_pct, vwap, score, stock, now, vol_surge)

    def _emit_signal(self, symbol, direction, ltp, target, stoploss,
                     gap_pct, vwap, score, stock, now, vol_surge):
        self.signals[symbol] = dict(
            symbol=symbol, direction=direction, entry=ltp, target=target,
            stoploss=stoploss, gap_pct=gap_pct, vwap=vwap, score=score,
            screens=stock.get("screen_count",0), cap=stock.get("cap","?"),
            vol_surge=vol_surge, time=now.strftime("%H:%M:%S")
        )
        emoji  = "🟢 LONG " if direction == "LONG" else "🔴 SHORT"
        pct    = abs(ltp-target)/ltp*100
        sl_pct = abs(ltp-stoploss)/ltp*100
        print(f"\n{'='*58}")
        print(f"  {emoji} SIGNAL — {symbol} [{stock.get('cap')}]")
        print(f"  {stock.get('company_name','')[:40]}")
        print(f"{'='*58}")
        print(f"  Entry:     ₹{ltp:,.2f}")
        print(f"  Target:    ₹{target:,.2f}  (+{pct:.1f}%)")
        print(f"  Stoploss:  ₹{stoploss:,.2f}  (-{sl_pct:.1f}%)")
        print(f"  Gap:       {gap_pct:+.2f}%  {'| Vol surge ✅' if vol_surge else ''}")
        print(f"  VWAP:      ₹{vwap:,.2f}")
        print(f"  Score:     {score:.1f} | Screens: {stock.get('screen_count',0)}")
        print(f"  Market:    {self.market_direction}")
        print(f"  Time:      {now.strftime('%H:%M:%S')}")
        print(f"  ⚠️  Square off by 3:15 PM")
        print(f"{'='*58}\n")

    def print_summary(self):
        longs  = [s for s in self.signals.values() if s["direction"] == "LONG"]
        shorts = [s for s in self.signals.values() if s["direction"] == "SHORT"]
        print(f"\n{'='*58}")
        print(f"  INTRADAY SUMMARY — {date.today()} | {self.market_direction}")
        print(f"{'='*58}")
        if longs:
            print(f"\n  🟢 LONG ({len(longs)}):")
            for s in longs:
                print(f"    {s['symbol']:<15} ₹{s['entry']:>8,.2f} → ₹{s['target']:>8,.2f} | SL ₹{s['stoploss']:,.2f}")
        if shorts:
            print(f"\n  🔴 SHORT ({len(shorts)}):")
            for s in shorts:
                print(f"    {s['symbol']:<15} ₹{s['entry']:>8,.2f} → ₹{s['target']:>8,.2f} | SL ₹{s['stoploss']:,.2f}")
        if not longs and not shorts:
            print("  No signals triggered today.")
        print(f"\n  ⚠️  SQUARE OFF ALL POSITIONS BY 3:15 PM")
        print(f"{'='*58}\n")


def main():
    if not API_KEY or not ACCESS_TOKEN:
        print("❌ Run: python scripts/kite_login.py first")
        sys.exit(1)

    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(ACCESS_TOKEN)

    # Full pre-market analysis
    market_direction, nifty_change = get_market_direction(kite)

    # Build watchlist
    long_list, short_list = get_intraday_watchlist()
    if market_direction == "BULLISH":
        short_list = []
    elif market_direction == "BEARISH":
        long_list = []

    all_stocks = long_list + short_list
    if not all_stocks:
        logger.error("No stocks in watchlist")
        sys.exit(1)

    symbols   = list(set(s["symbol"] for s in all_stocks))
    token_map = get_instrument_tokens(kite, symbols)
    prev_close= get_previous_close(kite, list(token_map.keys()))

    # Print watchlist
    print(f"\n{'='*58}")
    print(f"  investMITRA INTRADAY — {date.today()}")
    print(f"  Market: {market_direction} | Nifty Fut: {nifty_change:+.2f}%")
    print(f"{'='*58}")
    if long_list:
        print(f"\n  🟢 LONG CANDIDATES ({len(long_list)}):")
        print(f"  {'Symbol':<15} {'Score':>6} {'Screens':>8} {'Avg Vol':>10}")
        print(f"  {'─'*45}")
        for s in long_list:
            if s["symbol"] in token_map:
                print(f"  {s['symbol']:<15} {s['investmitra_score']:>6.1f} {s['screen_count']:>8} {s['avg_volume']:>10,}")
    if short_list:
        print(f"\n  🔴 SHORT CANDIDATES ({len(short_list)}):")
        for s in short_list:
            if s["symbol"] in token_map:
                print(f"  {s['symbol']:<15} {s['investmitra_score']:>6.1f} {s['screen_count']:>8} {s['avg_volume']:>10,}")
    print(f"\n  Signals after 9:30 AM | Gap >0.3% + VWAP + Volume")
    print(f"{'='*58}\n")

    tokens = list(token_map.values())
    engine = IntradayEngine(long_list, short_list, token_map, prev_close, market_direction)

    def on_connect(ws, response):
        logger.info("WebSocket connected — %d tokens", len(tokens))
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)

    ticker = KiteTicker(API_KEY, ACCESS_TOKEN)
    ticker.on_connect = on_connect
    ticker.on_ticks   = engine.on_tick
    ticker.on_close   = lambda ws, c, r: logger.warning("Closed: %s", r)
    ticker.on_error   = lambda ws, c, r: logger.error("Error: %s", r)

    logger.info("Live feed starting... signals after 9:30 AM")
    try:
        ticker.connect(threaded=True)
        while True:
            now = datetime.now(IST)
            if now.hour >= 15 and now.minute >= 20:
                engine.print_summary()
                logger.info("Market closed!")
                break
            time.sleep(10)
    except KeyboardInterrupt:
        engine.print_summary()


if __name__ == "__main__":
    main()
