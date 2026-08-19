"""
investMITRA — Intraday Signal Engine v6
All data sources integrated for sharp signal quality.

Pre-market filters (before 9:15 AM):
  1. Nifty Futures    — primary direction (BULLISH/BEARISH/NEUTRAL)
  2. India VIX        — fear filter (>20 = avoid, >16 = reduce size)
  3. SGX + Global     — confirmation
  4. Corporate events — skip stocks with results today
  5. NSE announcements— flag overnight news on watchlist stocks

Signal scoring (at 9:30 AM):
  Base: Gap + VWAP + Volume surge
  Boost: Screener screen count, investMITRA score, Piotroski
  Filter: No results today, no SEBI action, VIX < 20

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


def get_premarket_context() -> dict:
    """Load all pre-market context from Neon."""
    ctx = {
        "india_vix":        None,
        "vix_signal":       "NORMAL",
        "sgx_change":       None,
        "us_sentiment":     "UNKNOWN",
        "global_signal":    "NEUTRAL",
        "results_today":    set(),   # symbols with results today
        "announcements":    [],      # overnight NSE announcements
        "sebi_symbols":     set(),   # symbols in SEBI actions
    }

    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        cur  = conn.cursor()

        # India VIX
        cur.execute("""SELECT last_price, change_pct FROM investmitra.market_indices
                       WHERE index_name='INDIA VIX' AND fetch_date=CURRENT_DATE
                       ORDER BY fetched_at DESC LIMIT 1""")
        r = cur.fetchone()
        if r: ctx["india_vix"] = float(r[0])

        # SGX Nifty
        cur.execute("""SELECT change_str FROM investmitra.global_indices
                       WHERE index_name ILIKE '%SGX%NIFTY%' AND fetch_date=CURRENT_DATE
                       ORDER BY fetched_at DESC LIMIT 1""")
        r = cur.fetchone()
        if r:
            try: ctx["sgx_change"] = float(str(r[0]).replace('+','').replace('%',''))
            except: pass

        # US markets (Dow Jones)
        cur.execute("""SELECT change_str FROM investmitra.global_indices
                       WHERE index_name ILIKE '%DOW JONES%' AND fetch_date=CURRENT_DATE
                       ORDER BY fetched_at DESC LIMIT 1""")
        r = cur.fetchone()
        if r:
            try:
                chg = float(str(r[0]).replace('+','').replace(',',''))
                ctx["us_sentiment"] = "POSITIVE" if chg > 0 else "NEGATIVE" if chg < -200 else "MIXED"
            except: pass

        # Corporate events — stocks with results TODAY
        cur.execute("""SELECT UPPER(symbol) FROM investmitra.corporate_events
                       WHERE event_date = CURRENT_DATE
                       AND category = 'RESULTS'""")
        ctx["results_today"] = {r[0] for r in cur.fetchall()}

        # NSE announcements — last 12 hours, important ones
        cur.execute("""SELECT symbol, ann_type FROM investmitra.nse_announcements
                       WHERE ann_datetime >= NOW() - INTERVAL '12 hours'
                       AND is_important = TRUE
                       ORDER BY ann_datetime DESC LIMIT 20""")
        ctx["announcements"] = [(r[0], r[1]) for r in cur.fetchall()]

        # SEBI actions — last 7 days
        cur.execute("""SELECT UPPER(company_hint) FROM investmitra.sebi_updates
                       WHERE is_enforcement = TRUE
                       AND pub_date >= CURRENT_DATE - INTERVAL '7 days'""")
        ctx["sebi_symbols"] = {r[0][:10] for r in cur.fetchall() if r[0]}

        cur.close(); conn.close()

    except Exception as e:
        logger.warning("Pre-market context fetch failed: %s", e)

    # VIX signal
    vix = ctx["india_vix"]
    if vix:
        if vix < 12:   ctx["vix_signal"] = "CALM"
        elif vix < 16: ctx["vix_signal"] = "NORMAL"
        elif vix < 20: ctx["vix_signal"] = "ELEVATED"
        else:          ctx["vix_signal"] = "HIGH"

    # Global signal
    sgx = ctx["sgx_change"]
    us  = ctx["us_sentiment"]
    if sgx and sgx > 0.2 and us != "NEGATIVE":
        ctx["global_signal"] = "BULLISH"
    elif sgx and sgx < -0.2 and us == "NEGATIVE":
        ctx["global_signal"] = "BEARISH"

    return ctx


def print_premarket(ctx: dict, direction: str, fut_change: float, source: str):
    """Print pre-market dashboard."""
    print(f"\n{'='*60}")
    print(f"  investMITRA PRE-MARKET — {date.today()}")
    print(f"{'='*60}")

    vix = ctx["india_vix"]
    if vix:
        e = "🟢" if vix < 12 else "🟡" if vix < 16 else "🔴"
        print(f"  {e} India VIX:    {vix:.2f} ({ctx['vix_signal']})")

    sgx = ctx["sgx_change"]
    if sgx is not None:
        e = "🟢" if sgx > 0 else "🔴"
        print(f"  {e} SGX Nifty:   {sgx:+.2f}%")

    e = "🟢" if ctx["us_sentiment"]=="POSITIVE" else "🔴" if ctx["us_sentiment"]=="NEGATIVE" else "🟡"
    print(f"  {e} US Markets:  {ctx['us_sentiment']}")
    print(f"  🌍 Global:      {ctx['global_signal']}")

    e = "🟢" if fut_change > 0.3 else "🔴" if fut_change < -0.3 else "🟡"
    print(f"  {e} Nifty Fut:   {fut_change:+.2f}% ({source})")

    if ctx["results_today"]:
        print(f"\n  ⚠️  Results TODAY (skipped from watchlist):")
        for s in list(ctx["results_today"])[:5]:
            print(f"     {s}")

    if ctx["announcements"]:
        print(f"\n  📋 Overnight Announcements:")
        for sym, ann_type in ctx["announcements"][:5]:
            print(f"     {sym}: {ann_type[:40]}")

    if ctx["vix_signal"] == "HIGH":
        print(f"\n  🚨 HIGH VIX — AVOID INTRADAY OR REDUCE SIZE TO 25%")
    elif ctx["vix_signal"] == "ELEVATED":
        print(f"\n  ⚠️  Elevated VIX — Reduce position size to 50%")

    d_emoji = "🟢" if direction=="BULLISH" else "🔴" if direction=="BEARISH" else "🟡"
    advice  = "LONG signals only" if direction=="BULLISH" else "SHORT signals only" if direction=="BEARISH" else "Both LONG and SHORT"
    print(f"\n  {d_emoji} DIRECTION: {direction} → {advice}")
    print(f"{'='*60}\n")


def get_market_direction(kite: KiteConnect, ctx: dict) -> tuple[str, float]:
    """Get Nifty Futures direction and combine with global context."""
    fut_price  = 0
    prev_close = 0
    source     = "Nifty 50 Spot"

    try:
        instruments = kite.instruments("NFO")
        nifty_futs  = sorted(
            [i for i in instruments if i["name"]=="NIFTY" and i["instrument_type"]=="FUT"],
            key=lambda x: x["expiry"]
        )
        if nifty_futs:
            near     = nifty_futs[0]
            sym      = f"NFO:{near['tradingsymbol']}"
            quote    = kite.quote([sym])
            d        = quote.get(sym, {})
            prev_close = d.get("ohlc", {}).get("close", 0)
            fut_price  = d.get("last_price", 0)
            source     = f"Nifty Fut ({near['tradingsymbol']})"
    except Exception as e:
        logger.warning("Futures failed (%s) — spot fallback", e)
        try:
            q = kite.quote(["NSE:NIFTY 50"])
            d = q.get("NSE:NIFTY 50", {})
            prev_close = d.get("ohlc", {}).get("close", 0)
            fut_price  = d.get("last_price", 0)
        except: pass

    change_pct = (fut_price - prev_close) / prev_close * 100 if prev_close else 0

    if change_pct > 0.3:   fut_dir = "BULLISH"
    elif change_pct < -0.3: fut_dir = "BEARISH"
    else:                   fut_dir = "NEUTRAL"

    # Combine with global
    g = ctx["global_signal"]
    if fut_dir == "BULLISH" and g != "BEARISH":   direction = "BULLISH"
    elif fut_dir == "BEARISH" and g != "BULLISH":  direction = "BEARISH"
    else:                                           direction = "NEUTRAL"

    print_premarket(ctx, direction, change_pct, source)
    return direction, change_pct


def get_intraday_watchlist(ctx: dict) -> tuple[list[dict], list[dict]]:
    """
    Build watchlist filtering out:
    - Stocks with results today
    - Stocks in SEBI actions
    - Stocks with major overnight announcements
    """
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()

    # Stocks with major overnight NSE announcements (market sensitive)
    cur.execute("""SELECT UPPER(symbol) FROM investmitra.nse_announcements
                   WHERE ann_datetime >= NOW() - INTERVAL '12 hours'
                   AND is_sensitive = TRUE""")
    sensitive_stocks = {r[0] for r in cur.fetchall()}

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
               ROUND(av.avg_vol::numeric,0), ROUND(av.avg_price::numeric,2),
               COALESCE(ss.screen_count, 0),
               COALESCE(vq.piotroski_score, 0),
               COALESCE(vq.graham_criteria_met, 0)
        FROM investmitra.daily_scores ds
        JOIN investmitra.company_master cm ON ds.isin = cm.isin
        JOIN avg_volume av ON ds.isin = av.isin
        LEFT JOIN (
            SELECT isin, COUNT(DISTINCT screen_name) AS screen_count
            FROM investmitra.screener_signals
            WHERE signal_date = (SELECT MAX(signal_date) FROM investmitra.screener_signals)
            GROUP BY isin
        ) ss ON ds.isin = ss.isin
        LEFT JOIN investmitra.value_quality vq ON ds.isin = vq.isin
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
        symbol = r[2]
        if not symbol: continue

        # Skip filters
        if symbol.upper() in ctx["results_today"]:
            logger.info("Skipping %s — results today", symbol)
            continue
        if symbol.upper() in sensitive_stocks:
            logger.info("Skipping %s — sensitive NSE announcement", symbol)
            continue

        stock = {
            "isin":              r[0],
            "company_name":      r[1],
            "symbol":            symbol,
            "sector":            r[3],
            "cap":               r[4],
            "investmitra_score": float(r[5] or 0),
            "signal":            r[6],
            "momentum_score":    float(r[7] or 0),
            "avg_volume":        int(r[8] or 0),
            "avg_price":         float(r[9] or 0),
            "screen_count":      int(r[10] or 0),
            "piotroski":         int(r[11] or 0),
            "graham":            int(r[12] or 0),
        }

        score = stock["investmitra_score"]
        if score >= 60:
            long_list.append(stock)
        elif score <= 35:
            short_list.append(stock)

    # Rank by combined quality signal
    def quality_rank(s):
        return (s["investmitra_score"] * 0.5 +
                s["screen_count"] * 2.0 +
                s["piotroski"] * 1.5 +
                s["graham"] * 3.0)

    long_list  = sorted(long_list,  key=quality_rank, reverse=True)[:15]
    short_list = sorted(short_list, key=lambda x: x["investmitra_score"])[:10]

    return long_list, short_list


def get_instrument_tokens(kite, symbols):
    try:
        instruments = kite.instruments("NSE")
        return {i["tradingsymbol"]: i["instrument_token"]
                for i in instruments if i["tradingsymbol"] in symbols and i["segment"] == "NSE"}
    except Exception as e:
        logger.error("Instruments: %s", e); return {}


def get_previous_close(kite, symbols):
    try:
        quotes = kite.quote([f"NSE:{s}" for s in symbols])
        return {s.replace("NSE:", ""): d["ohlc"]["close"] for s, d in quotes.items()}
    except Exception as e:
        logger.error("Quote: %s", e); return {}


class IntradayEngine:
    def __init__(self, long_list, short_list, token_map, prev_close, market_direction, ctx):
        self.long_map         = {s["symbol"]: s for s in long_list}
        self.short_map        = {s["symbol"]: s for s in short_list}
        self.all_stocks       = {**self.long_map, **self.short_map}
        self.token_map        = token_map
        self.rev_tokens       = {v: k for k, v in token_map.items()}
        self.prev_close       = prev_close
        self.market_direction = market_direction
        self.vix_signal       = ctx["vix_signal"]
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
        # Skip if VIX too high
        if self.vix_signal == "HIGH": return

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

        # Tighter gap threshold if VIX elevated
        gap_threshold = 0.5 if self.vix_signal == "ELEVATED" else 0.3

        if symbol in self.long_map and can_long and gap_pct > gap_threshold and above_vwap and score >= 60:
            self._emit(symbol, "LONG", ltp, round(ltp*1.015,2), round(ltp*0.995,2),
                       gap_pct, vwap, score, stock, now, vol_surge)
        elif symbol in self.short_map and can_short and gap_pct < -gap_threshold and below_vwap and score <= 40:
            self._emit(symbol, "SHORT", ltp, round(ltp*0.985,2), round(ltp*1.005,2),
                       gap_pct, vwap, score, stock, now, vol_surge)

    def _emit(self, symbol, direction, ltp, target, stoploss,
              gap_pct, vwap, score, stock, now, vol_surge):
        self.signals[symbol] = dict(
            symbol=symbol, direction=direction, entry=ltp,
            target=target, stoploss=stoploss, gap_pct=gap_pct,
            vwap=vwap, score=score, time=now.strftime("%H:%M:%S"),
            screens=stock.get("screen_count",0), cap=stock.get("cap","?"),
            piotroski=stock.get("piotroski",0), graham=stock.get("graham",0),
        )
        emoji  = "🟢 LONG " if direction == "LONG" else "🔴 SHORT"
        pct    = abs(ltp-target)/ltp*100
        sl_pct = abs(ltp-stoploss)/ltp*100
        size_note = " | REDUCE SIZE 50%" if self.vix_signal == "ELEVATED" else ""

        print(f"\n{'='*60}")
        print(f"  {emoji} SIGNAL — {symbol} [{stock.get('cap')}]{size_note}")
        print(f"  {stock.get('company_name','')[:45]}")
        print(f"{'='*60}")
        print(f"  Entry:     ₹{ltp:,.2f}")
        print(f"  Target:    ₹{target:,.2f}  (+{pct:.1f}%)")
        print(f"  Stoploss:  ₹{stoploss:,.2f}  (-{sl_pct:.1f}%)")
        print(f"  Gap:       {gap_pct:+.2f}%  {'| Vol surge ✅' if vol_surge else ''}")
        print(f"  VWAP:      ₹{vwap:,.2f}")
        print(f"  Score:     {score:.1f} | Screens: {stock.get('screen_count',0)} | F-Score: {stock.get('piotroski',0)}")
        print(f"  Market:    {self.market_direction} | VIX: {self.vix_signal}")
        print(f"  Time:      {now.strftime('%H:%M:%S')}")
        print(f"  ⚠️  Square off by 3:15 PM")
        print(f"{'='*60}\n")

    def print_summary(self):
        longs  = [s for s in self.signals.values() if s["direction"] == "LONG"]
        shorts = [s for s in self.signals.values() if s["direction"] == "SHORT"]
        print(f"\n{'='*60}")
        print(f"  INTRADAY SUMMARY — {date.today()} | {self.market_direction} | VIX: {self.vix_signal}")
        print(f"{'='*60}")
        for label, lst in [("🟢 LONG", longs), ("🔴 SHORT", shorts)]:
            if lst:
                print(f"\n  {label} ({len(lst)}):")
                for s in lst:
                    print(f"    {s['symbol']:<15} ₹{s['entry']:>8,.2f} → ₹{s['target']:>8,.2f} | SL ₹{s['stoploss']:,.2f}")
        if not longs and not shorts:
            print("  No signals triggered today.")
        print(f"\n  ⚠️  SQUARE OFF ALL POSITIONS BY 3:15 PM")
        print(f"{'='*60}\n")


def main():
    if not API_KEY or not ACCESS_TOKEN:
        print("❌ Run: python scripts/kite_login.py first")
        sys.exit(1)

    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(ACCESS_TOKEN)

    # Full pre-market context
    ctx              = get_premarket_context()
    market_direction, nifty_change = get_market_direction(kite, ctx)

    # Build filtered watchlist
    long_list, short_list = get_intraday_watchlist(ctx)

    if market_direction == "BULLISH":   short_list = []
    elif market_direction == "BEARISH": long_list  = []

    if ctx["vix_signal"] == "HIGH":
        print("🚨 VIX > 20 — No intraday signals today. Exiting.")
        sys.exit(0)

    all_stocks = long_list + short_list
    if not all_stocks:
        logger.error("No stocks in watchlist")
        sys.exit(1)

    symbols   = list(set(s["symbol"] for s in all_stocks))
    token_map = get_instrument_tokens(kite, symbols)
    prev_close= get_previous_close(kite, list(token_map.keys()))

    # Print watchlist
    print(f"\n{'='*60}")
    print(f"  INTRADAY WATCHLIST — {date.today()} | {market_direction}")
    print(f"{'='*60}")
    if long_list:
        print(f"\n  🟢 LONG ({len(long_list)}) — ranked by quality:")
        print(f"  {'Symbol':<15} {'Score':>6} {'Screens':>8} {'F-Score':>8} {'Avg Vol':>10}")
        print(f"  {'─'*55}")
        for s in long_list:
            if s["symbol"] in token_map:
                print(f"  {s['symbol']:<15} {s['investmitra_score']:>6.1f} {s['screen_count']:>8} "
                      f"{s['piotroski']:>8} {s['avg_volume']:>10,}")
    if short_list:
        print(f"\n  🔴 SHORT ({len(short_list)}):")
        for s in short_list:
            if s["symbol"] in token_map:
                print(f"  {s['symbol']:<15} {s['investmitra_score']:>6.1f}")
    print(f"\n  Gap threshold: {'0.5%' if ctx['vix_signal']=='ELEVATED' else '0.3%'} (VIX: {ctx['vix_signal']})")
    print(f"  Signals after 9:30 AM")
    print(f"{'='*60}\n")

    tokens = list(token_map.values())
    engine = IntradayEngine(long_list, short_list, token_map, prev_close, market_direction, ctx)

    ticker = KiteTicker(API_KEY, ACCESS_TOKEN)
    ticker.on_connect = lambda ws, r: (ws.subscribe(tokens), ws.set_mode(ws.MODE_FULL, tokens))
    ticker.on_ticks   = engine.on_tick
    ticker.on_close   = lambda ws, c, r: logger.warning("Closed: %s", r)
    ticker.on_error   = lambda ws, c, r: logger.error("Error: %s", r)

    logger.info("Live feed starting...")
    try:
        ticker.connect(threaded=True)
        while True:
            now = datetime.now(IST)
            if now.hour >= 15 and now.minute >= 20:
                engine.print_summary(); break
            time.sleep(10)
    except KeyboardInterrupt:
        engine.print_summary()


if __name__ == "__main__":
    main()
