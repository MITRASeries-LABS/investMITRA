"""
investMITRA — Intraday Signal Engine v4
Uses Nifty Futures (nearest expiry) for pre-market direction.
Falls back to Nifty 50 spot if futures unavailable.

Market direction filter:
  BULLISH (futures +0.3%+) → LONG signals only
  BEARISH (futures -0.3%+) → SHORT signals only
  NEUTRAL (flat)           → both LONG and SHORT

Watchlist: MID/LARGE cap, avg vol > 2L, price ₹50-5000
Signal: Gap 0.3%+ | VWAP confirmation | Volume surge
Target: 1.5% | Stoploss: 0.5% | RR: 3:1
Square off: 3:15 PM mandatory

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


def get_market_direction(kite: KiteConnect) -> tuple[str, float]:
    """
    Fetch Nifty Futures (nearest expiry) for true pre-market direction.
    Falls back to Nifty 50 spot if futures unavailable.
    Returns: (direction, change_pct)
    """
    try:
        # Try Nifty Futures first — true pre-market indicator like GIFT Nifty
        instruments = kite.instruments("NFO")
        nifty_futs  = [i for i in instruments
                       if i["name"] == "NIFTY" and i["instrument_type"] == "FUT"]
        nifty_futs.sort(key=lambda x: x["expiry"])

        if nifty_futs:
            near_fut   = nifty_futs[0]
            fut_symbol = f"NFO:{near_fut['tradingsymbol']}"
            logger.info("Using Nifty Futures: %s (expiry: %s)",
                        near_fut['tradingsymbol'], near_fut['expiry'])
            quote      = kite.quote([fut_symbol])
            fut_data   = quote.get(fut_symbol, {})
            prev_close = fut_data.get("ohlc", {}).get("close", 0)
            last_price = fut_data.get("last_price", 0)
            source     = f"Nifty Fut ({near_fut['tradingsymbol']})"
        else:
            raise Exception("No Nifty futures found")

    except Exception as e:
        logger.warning("Futures fetch failed (%s) — using Nifty 50 spot", e)
        try:
            quote      = kite.quote(["NSE:NIFTY 50"])
            spot       = quote.get("NSE:NIFTY 50", {})
            prev_close = spot.get("ohlc", {}).get("close", 0)
            last_price = spot.get("last_price", 0)
            source     = "Nifty 50 Spot"
        except:
            return "NEUTRAL", 0.0

    if not prev_close or not last_price:
        return "NEUTRAL", 0.0

    change_pct = (last_price - prev_close) / prev_close * 100

    if change_pct > 0.3:
        direction = "BULLISH"
        emoji     = "🟢"
        advice    = "Taking LONG signals only"
    elif change_pct < -0.3:
        direction = "BEARISH"
        emoji     = "🔴"
        advice    = "Taking SHORT signals only"
    else:
        direction = "NEUTRAL"
        emoji     = "🟡"
        advice    = "Taking both LONG and SHORT signals"

    print(f"\n{'='*58}")
    print(f"  {emoji} MARKET DIRECTION: {direction}")
    print(f"  Source:    {source}")
    print(f"  Last:      ₹{last_price:,.2f}  ({change_pct:+.2f}%)")
    print(f"  Prev Close:₹{prev_close:,.2f}")
    print(f"  Strategy:  {advice}")
    print(f"{'='*58}\n")

    return direction, change_pct


def get_intraday_watchlist() -> tuple[list[dict], list[dict]]:
    """Get LONG and SHORT candidate watchlists from Neon."""
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()

    cur.execute("""
        WITH avg_volume AS (
            SELECT isin,
                   AVG(volume) AS avg_vol,
                   AVG(close)  AS avg_price
            FROM investmitra.equity_prices
            WHERE trade_date >= CURRENT_DATE - INTERVAL '30 days'
            GROUP BY isin
            HAVING AVG(volume) > 200000
               AND AVG(close) BETWEEN 50 AND 5000
        )
        SELECT ds.isin, ds.company_name, cm.nse_symbol, ds.sector,
               cm.market_cap_category,
               ds.investmitra_score, ds.signal,
               ds.momentum_score,
               ROUND(av.avg_vol::numeric, 0) AS avg_volume,
               ROUND(av.avg_price::numeric, 2) AS avg_price,
               COALESCE(ss.screen_count, 0) AS screen_count
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

    long_list  = []
    short_list = []

    for r in rows:
        stock = {
            "isin":              r[0],
            "company_name":      r[1],
            "symbol":            r[2],
            "sector":            r[3],
            "cap":               r[4],
            "investmitra_score": float(r[5] or 0),
            "signal":            r[6],
            "momentum_score":    float(r[7] or 0),
            "avg_volume":        int(r[8] or 0),
            "avg_price":         float(r[9] or 0),
            "screen_count":      int(r[10] or 0),
        }
        score = stock["investmitra_score"]
        if score >= 60:
            long_list.append(stock)
        elif score <= 35:
            short_list.append(stock)

    long_list  = sorted(long_list,
                        key=lambda x: x["investmitra_score"] * 0.6 + x["screen_count"] * 2,
                        reverse=True)[:15]
    short_list = sorted(short_list, key=lambda x: x["investmitra_score"])[:10]

    return long_list, short_list


def get_instrument_tokens(kite: KiteConnect, symbols: list[str]) -> dict[str, int]:
    try:
        instruments = kite.instruments("NSE")
        token_map   = {}
        for inst in instruments:
            if inst["tradingsymbol"] in symbols and inst["segment"] == "NSE":
                token_map[inst["tradingsymbol"]] = inst["instrument_token"]
        logger.info("Mapped %d/%d symbols", len(token_map), len(symbols))
        return token_map
    except Exception as e:
        logger.error("Instruments failed: %s", e)
        return {}


def get_previous_close(kite: KiteConnect, symbols: list[str]) -> dict[str, float]:
    try:
        nse_symbols = [f"NSE:{s}" for s in symbols]
        quotes      = kite.quote(nse_symbols)
        return {
            s.replace("NSE:", ""): data["ohlc"]["close"]
            for s, data in quotes.items()
        }
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

        self.ticks      = defaultdict(list)
        self.vwap       = defaultdict(float)
        self.cum_vol    = defaultdict(int)
        self.cum_tp_vol = defaultdict(float)
        self.first_tick = defaultdict(bool)
        self.signals    = {}

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

            # Update VWAP
            prev_vol = self.cum_vol[symbol]
            new_vol  = max(0, volume - prev_vol)
            if new_vol > 0:
                self.cum_vol[symbol]    = volume
                self.cum_tp_vol[symbol] += ltp * new_vol
                if volume > 0:
                    self.vwap[symbol] = self.cum_tp_vol[symbol] / volume

            mkt_min = now.hour * 60 + now.minute
            if mkt_min >= 9 * 60 + 30:
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

        mkt_min   = now.hour * 60 + now.minute - (9 * 60 + 15)
        frac_day  = max(mkt_min / 375, 0.1)
        exp_vol   = avg_vol * frac_day
        vol_surge = volume > exp_vol * 1.5 if exp_vol > 0 else False

        # Market direction filter
        can_long  = self.market_direction in ("BULLISH", "NEUTRAL")
        can_short = self.market_direction in ("BEARISH", "NEUTRAL")

        # LONG signal
        if (symbol in self.long_map and can_long and
                gap_pct > 0.3 and above_vwap and score >= 60):
            target   = round(ltp * 1.015, 2)
            stoploss = round(ltp * 1.005, 2) if False else round(ltp * 0.995, 2)
            self._emit_signal(symbol, "LONG", ltp, target, stoploss,
                              gap_pct, vwap, score, stock, now, vol_surge)

        # SHORT signal
        elif (symbol in self.short_map and can_short and
                gap_pct < -0.3 and below_vwap and score <= 40):
            target   = round(ltp * 0.985, 2)
            stoploss = round(ltp * 1.005, 2)
            self._emit_signal(symbol, "SHORT", ltp, target, stoploss,
                              gap_pct, vwap, score, stock, now, vol_surge)

    def _emit_signal(self, symbol, direction, ltp, target, stoploss,
                     gap_pct, vwap, score, stock, now, vol_surge):
        self.signals[symbol] = {
            "symbol": symbol, "direction": direction,
            "entry": ltp, "target": target, "stoploss": stoploss,
            "gap_pct": gap_pct, "vwap": vwap, "score": score,
            "screens": stock.get("screen_count", 0),
            "cap": stock.get("cap", "?"),
            "vol_surge": vol_surge,
            "time": now.strftime("%H:%M:%S"),
        }

        emoji  = "🟢 LONG " if direction == "LONG" else "🔴 SHORT"
        pct    = abs(ltp - target) / ltp * 100
        sl_pct = abs(ltp - stoploss) / ltp * 100

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

    # Get market direction from Nifty Futures
    market_direction, nifty_change = get_market_direction(kite)

    # Get watchlist
    logger.info("Building intraday watchlist...")
    long_list, short_list = get_intraday_watchlist()

    # Filter based on market direction
    if market_direction == "BULLISH":
        logger.info("Market BULLISH — LONG candidates only")
        short_list = []
    elif market_direction == "BEARISH":
        logger.info("Market BEARISH — SHORT candidates only")
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
        print(f"  {'Symbol':<15} {'Score':>6} {'Screens':>8} {'Avg Vol':>10} {'Price':>8}")
        print(f"  {'─'*52}")
        for s in long_list:
            if s["symbol"] in token_map:
                print(f"  {s['symbol']:<15} {s['investmitra_score']:>6.1f} {s['screen_count']:>8} "
                      f"{s['avg_volume']:>10,} ₹{s['avg_price']:>7,.0f}")

    if short_list:
        print(f"\n  🔴 SHORT CANDIDATES ({len(short_list)}):")
        print(f"  {'Symbol':<15} {'Score':>6} {'Screens':>8} {'Avg Vol':>10} {'Price':>8}")
        print(f"  {'─'*52}")
        for s in short_list:
            if s["symbol"] in token_map:
                print(f"  {s['symbol']:<15} {s['investmitra_score']:>6.1f} {s['screen_count']:>8} "
                      f"{s['avg_volume']:>10,} ₹{s['avg_price']:>7,.0f}")

    print(f"\n  Signals appear after 9:30 AM")
    print(f"  Gap threshold: LONG >0.3% | SHORT <-0.3%")
    print(f"{'='*58}\n")

    tokens = list(token_map.values())
    engine = IntradayEngine(long_list, short_list, token_map, prev_close, market_direction)

    def on_connect(ws, response):
        logger.info("WebSocket connected — %d tokens", len(tokens))
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)

    def on_tick(ws, ticks):
        engine.on_tick(ws, ticks)

    def on_close(ws, code, reason):
        logger.warning("WebSocket closed: %s", reason)

    def on_error(ws, code, reason):
        logger.error("WebSocket error: %s", reason)

    ticker = KiteTicker(API_KEY, ACCESS_TOKEN)
    ticker.on_connect = on_connect
    ticker.on_ticks   = on_tick
    ticker.on_close   = on_close
    ticker.on_error   = on_error

    logger.info("Starting live feed... signals after 9:30 AM")
    try:
        ticker.connect(threaded=True)
        while True:
            now = datetime.now(IST)
            if now.hour >= 15 and now.minute >= 20:
                engine.print_summary()
                logger.info("Market closed — square off all positions!")
                break
            time.sleep(10)
    except KeyboardInterrupt:
        engine.print_summary()


if __name__ == "__main__":
    main()
