"""
investMITRA — Intraday Signal Engine v7
Two-score architecture: Stock Quality (40%) + Intraday Opportunity (60%)

Quality Score (40%):
  investMITRA + Screener screens + Piotroski + Graham

Opportunity Score (60%):
  Gap quality, RVOL, VWAP position, Opening Range Break,
  Sector relative strength, Market regime, Key level distance

Risk Management:
  ATR-based stops (not fixed %)
  Position sizing: fixed ₹ risk / stop distance
  Daily kill-switch: max loss, max trades, max consecutive losses
  Time-of-day session logic (4 sessions)

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

# ── Risk Parameters ───────────────────────────────────────────────────────────
MAX_RISK_PER_TRADE_INR = 2000    # Max loss per trade in ₹
MAX_DAILY_LOSS_INR     = 6000    # Kill switch — stop trading
MAX_POSITIONS          = 3       # Max simultaneous positions
MAX_CONSECUTIVE_LOSSES = 2       # Stop after N consecutive losses
ATR_STOP_MULTIPLIER    = 1.5     # Stop = ATR * multiplier
ATR_TARGET_MULTIPLIER  = 3.0     # Target = ATR * multiplier (2:1 RR)

# ── Session Times (IST) ───────────────────────────────────────────────────────
SESSIONS = {
    "opening":   (9*60+15, 9*60+30),   # 9:15-9:30 — volatile, no signals
    "momentum":  (9*60+30, 11*60+30),  # 9:30-11:30 — best signals
    "choppy":    (11*60+30, 13*60+30), # 11:30-1:30 — reduced quality
    "afternoon": (13*60+30, 15*60+0),  # 1:30-3:00 — renewed activity
    "closing":   (15*60+0, 15*60+30),  # 3:00-3:30 — exit only
}

# Gap quality thresholds by session
GAP_THRESHOLDS = {
    "momentum":  0.3,
    "choppy":    0.6,   # Tighter in choppy session
    "afternoon": 0.4,
}


def get_current_session(now: datetime) -> str:
    mkt_min = now.hour * 60 + now.minute
    for session, (start, end) in SESSIONS.items():
        if start <= mkt_min < end:
            return session
    return "closed"


def get_premarket_context() -> dict:
    ctx = {
        "india_vix":     None,
        "vix_signal":    "NORMAL",
        "sgx_change":    None,
        "us_sentiment":  "UNKNOWN",
        "global_signal": "NEUTRAL",
        "results_today": set(),
        "announcements": [],
    }
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        cur  = conn.cursor()

        cur.execute("SELECT last_price FROM investmitra.market_indices WHERE index_name='INDIA VIX' AND fetch_date=CURRENT_DATE ORDER BY fetched_at DESC LIMIT 1")
        r = cur.fetchone()
        if r: ctx["india_vix"] = float(r[0])

        cur.execute("SELECT change_str FROM investmitra.global_indices WHERE index_name ILIKE '%SGX%NIFTY%' AND fetch_date=CURRENT_DATE ORDER BY fetched_at DESC LIMIT 1")
        r = cur.fetchone()
        if r:
            try: ctx["sgx_change"] = float(str(r[0]).replace('+','').replace('%',''))
            except: pass

        cur.execute("SELECT change_str FROM investmitra.global_indices WHERE index_name ILIKE '%DOW JONES%' AND fetch_date=CURRENT_DATE ORDER BY fetched_at DESC LIMIT 1")
        r = cur.fetchone()
        if r:
            try:
                chg = float(str(r[0]).replace('+','').replace(',',''))
                ctx["us_sentiment"] = "POSITIVE" if chg > 0 else "NEGATIVE" if chg < -200 else "MIXED"
            except: pass

        cur.execute("SELECT UPPER(symbol) FROM investmitra.corporate_events WHERE event_date=CURRENT_DATE AND category='RESULTS'")
        ctx["results_today"] = {r[0] for r in cur.fetchall()}

        cur.execute("SELECT symbol, ann_type FROM investmitra.nse_announcements WHERE ann_datetime >= NOW() - INTERVAL '12 hours' AND is_important=TRUE ORDER BY ann_datetime DESC LIMIT 10")
        ctx["announcements"] = [(r[0], r[1]) for r in cur.fetchall()]

        cur.close(); conn.close()
    except Exception as e:
        logger.warning("Context fetch: %s", e)

    vix = ctx["india_vix"]
    if vix:
        if vix < 12:   ctx["vix_signal"] = "CALM"
        elif vix < 16: ctx["vix_signal"] = "NORMAL"
        elif vix < 20: ctx["vix_signal"] = "ELEVATED"
        else:          ctx["vix_signal"] = "HIGH"

    sgx = ctx["sgx_change"]
    us  = ctx["us_sentiment"]
    if sgx and sgx > 0.2 and us != "NEGATIVE": ctx["global_signal"] = "BULLISH"
    elif sgx and sgx < -0.2 and us == "NEGATIVE": ctx["global_signal"] = "BEARISH"

    return ctx


def get_market_direction(kite: KiteConnect, ctx: dict) -> tuple[str, float]:
    fut_price = prev_close = 0
    source = "Nifty 50 Spot"
    try:
        instruments = kite.instruments("NFO")
        nifty_futs  = sorted([i for i in instruments if i["name"]=="NIFTY" and i["instrument_type"]=="FUT"], key=lambda x: x["expiry"])
        if nifty_futs:
            near = nifty_futs[0]
            sym  = f"NFO:{near['tradingsymbol']}"
            q    = kite.quote([sym]).get(sym, {})
            prev_close = q.get("ohlc", {}).get("close", 0)
            fut_price  = q.get("last_price", 0)
            source     = f"Nifty Fut ({near['tradingsymbol']})"
    except:
        try:
            q = kite.quote(["NSE:NIFTY 50"]).get("NSE:NIFTY 50", {})
            prev_close = q.get("ohlc", {}).get("close", 0)
            fut_price  = q.get("last_price", 0)
        except: pass

    change_pct = (fut_price - prev_close) / prev_close * 100 if prev_close else 0

    if change_pct > 0.3:    fut_dir = "BULLISH"
    elif change_pct < -0.3: fut_dir = "BEARISH"
    else:                    fut_dir = "NEUTRAL"

    g = ctx["global_signal"]
    if fut_dir == "BULLISH" and g != "BEARISH":   direction = "BULLISH"
    elif fut_dir == "BEARISH" and g != "BULLISH":  direction = "BEARISH"
    else:                                           direction = "NEUTRAL"

    vix = ctx["india_vix"]
    print(f"\n{'='*62}")
    print(f"  investMITRA PRE-MARKET — {date.today()}")
    print(f"{'='*62}")
    if vix:
        e = "🟢" if vix<12 else "🟡" if vix<16 else "🔴"
        print(f"  {e} India VIX:   {vix:.2f} ({ctx['vix_signal']})")
    sgx = ctx["sgx_change"]
    if sgx is not None:
        print(f"  {'🟢' if sgx>0 else '🔴'} SGX Nifty:  {sgx:+.2f}%")
    print(f"  {'🟢' if ctx['us_sentiment']=='POSITIVE' else '🔴' if ctx['us_sentiment']=='NEGATIVE' else '🟡'} US Markets: {ctx['us_sentiment']}")
    print(f"  {'🟢' if change_pct>0.3 else '🔴' if change_pct<-0.3 else '🟡'} {source}: {change_pct:+.2f}%")
    if ctx["results_today"]:
        print(f"\n  ⚠️  Results TODAY (auto-skipped): {', '.join(list(ctx['results_today'])[:5])}")
    d_e = "🟢" if direction=="BULLISH" else "🔴" if direction=="BEARISH" else "🟡"
    print(f"\n  {d_e} DIRECTION: {direction}")
    if ctx["vix_signal"] == "HIGH":
        print(f"  🚨 HIGH VIX — AVOIDING INTRADAY")
    print(f"{'='*62}\n")

    return direction, change_pct


def get_historical_rvol_baseline() -> dict[str, list[float]]:
    """Load historical volume by 30-min bucket from Neon for RVOL calculation."""
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        cur  = conn.cursor()
        # Get avg volume per stock for last 20 trading days
        cur.execute("""
            SELECT isin, AVG(volume) AS avg_daily_vol
            FROM investmitra.equity_prices
            WHERE trade_date >= CURRENT_DATE - INTERVAL '30 days'
              AND trade_date < CURRENT_DATE
            GROUP BY isin
            HAVING AVG(volume) > 0
        """)
        result = {r[0]: float(r[1]) for r in cur.fetchall()}
        cur.close(); conn.close()
        return result
    except Exception as e:
        logger.warning("RVOL baseline: %s", e)
        return {}


def compute_atr(prices: list[float], period: int = 14) -> float:
    """Compute ATR from a list of recent prices (using range as proxy)."""
    if len(prices) < 2: return 0
    ranges = [abs(prices[i] - prices[i-1]) for i in range(1, len(prices))]
    return sum(ranges[-period:]) / min(len(ranges), period)


def get_intraday_watchlist(ctx: dict) -> tuple[list[dict], list[dict]]:
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()

    cur.execute("""SELECT UPPER(symbol) FROM investmitra.nse_announcements
                   WHERE ann_datetime >= NOW() - INTERVAL '12 hours' AND is_sensitive=TRUE""")
    sensitive = {r[0] for r in cur.fetchall()}

    cur.execute("""
        WITH avg_volume AS (
            SELECT isin, AVG(volume) AS avg_vol, AVG(close) AS avg_price,
                   STDDEV(close) AS price_std
            FROM investmitra.equity_prices
            WHERE trade_date >= CURRENT_DATE - INTERVAL '30 days'
            GROUP BY isin
            HAVING AVG(volume) > 200000 AND AVG(close) BETWEEN 50 AND 5000
        )
        SELECT ds.isin, ds.company_name, cm.nse_symbol, ds.sector,
               cm.market_cap_category, ds.investmitra_score, ds.signal,
               ds.momentum_score,
               ROUND(av.avg_vol::numeric,0), ROUND(av.avg_price::numeric,2),
               COALESCE(ss.screen_count,0),
               COALESCE(vq.piotroski_score,0), COALESCE(vq.graham_criteria_met,0),
               ROUND(av.price_std::numeric,2)
        FROM investmitra.daily_scores ds
        JOIN investmitra.company_master cm ON ds.isin=cm.isin
        JOIN avg_volume av ON ds.isin=av.isin
        LEFT JOIN (SELECT isin, COUNT(DISTINCT screen_name) AS screen_count
                   FROM investmitra.screener_signals
                   WHERE signal_date=(SELECT MAX(signal_date) FROM investmitra.screener_signals)
                   GROUP BY isin) ss ON ds.isin=ss.isin
        LEFT JOIN investmitra.value_quality vq ON ds.isin=vq.isin
        WHERE ds.score_date=(SELECT MAX(score_date) FROM investmitra.daily_scores)
          AND cm.nse_symbol IS NOT NULL
          AND cm.market_cap_category IN ('MID','LARGE')
        ORDER BY ds.investmitra_score DESC
    """)
    rows = cur.fetchall()
    cur.close(); conn.close()

    long_list = []
    short_list = []

    for r in rows:
        symbol = r[2]
        if not symbol: continue
        if symbol.upper() in ctx["results_today"]: continue
        if symbol.upper() in sensitive: continue

        inv_score = float(r[5] or 0)
        screens   = int(r[10] or 0)
        piotroski = int(r[11] or 0)
        graham    = int(r[12] or 0)
        price_std = float(r[13] or 0)

        # Stock Quality Score (40%)
        quality = (
            (inv_score / 100) * 0.50 +
            min(screens / 20, 1.0) * 0.20 +
            (piotroski / 9) * 0.15 +
            (graham / 4) * 0.15
        ) * 100

        stock = {
            "isin":          r[0], "company_name": r[1],
            "symbol":        symbol, "sector": r[3], "cap": r[4],
            "investmitra_score": inv_score, "signal": r[6],
            "momentum_score": float(r[7] or 0),
            "avg_volume":    int(r[8] or 0), "avg_price": float(r[9] or 0),
            "screen_count":  screens, "piotroski": piotroski, "graham": graham,
            "quality_score": round(quality, 2),
            "price_std":     price_std,
        }

        if inv_score >= 60:   long_list.append(stock)
        elif inv_score <= 35: short_list.append(stock)

    long_list  = sorted(long_list,  key=lambda x: x["quality_score"], reverse=True)[:15]
    short_list = sorted(short_list, key=lambda x: x["investmitra_score"])[:10]
    return long_list, short_list


def get_instrument_tokens(kite, symbols):
    try:
        return {i["tradingsymbol"]: i["instrument_token"]
                for i in kite.instruments("NSE")
                if i["tradingsymbol"] in symbols and i["segment"] == "NSE"}
    except Exception as e:
        logger.error("Instruments: %s", e); return {}


def get_previous_close(kite, symbols):
    try:
        quotes = kite.quote([f"NSE:{s}" for s in symbols])
        return {s.replace("NSE:",""): d["ohlc"]["close"] for s,d in quotes.items()}
    except Exception as e:
        logger.error("Quote: %s", e); return {}


class DailyRiskManager:
    """Hard risk controls — independent of signal quality."""
    def __init__(self):
        self.daily_pnl          = 0.0
        self.trades_today       = 0
        self.consecutive_losses = 0
        self.positions          = {}  # symbol -> entry

    def can_trade(self) -> tuple[bool, str]:
        if self.daily_pnl <= -MAX_DAILY_LOSS_INR:
            return False, f"Daily loss limit hit (₹{self.daily_pnl:.0f})"
        if self.trades_today >= MAX_POSITIONS * 3:
            return False, f"Max trades reached ({self.trades_today})"
        if len(self.positions) >= MAX_POSITIONS:
            return False, f"Max positions open ({len(self.positions)})"
        if self.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            return False, f"Consecutive losses: {self.consecutive_losses}"
        return True, "OK"

    def open_position(self, symbol, entry, stop, size):
        self.positions[symbol] = {"entry": entry, "stop": stop, "size": size}
        self.trades_today += 1

    def close_position(self, symbol, exit_price):
        if symbol not in self.positions: return
        pos = self.positions.pop(symbol)
        pnl = (exit_price - pos["entry"]) * pos["size"]
        self.daily_pnl += pnl
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        return pnl


class IntradayEngine:
    def __init__(self, long_list, short_list, token_map, prev_close,
                 market_direction, ctx, rvol_baseline):
        self.long_map         = {s["symbol"]: s for s in long_list}
        self.short_map        = {s["symbol"]: s for s in short_list}
        self.all_stocks       = {**self.long_map, **self.short_map}
        self.token_map        = token_map
        self.rev_tokens       = {v: k for k, v in token_map.items()}
        self.prev_close       = prev_close
        self.market_direction = market_direction
        self.vix_signal       = ctx["vix_signal"]
        self.rvol_baseline    = rvol_baseline

        # Per-symbol tracking
        self.vwap             = defaultdict(float)
        self.cum_vol          = defaultdict(int)
        self.cum_tp_vol       = defaultdict(float)
        self.prices           = defaultdict(list)   # recent prices for ATR
        self.or_high          = defaultdict(float)  # opening range high
        self.or_low           = defaultdict(lambda: float('inf'))
        self.or_set           = defaultdict(bool)   # OR confirmed at 9:30

        self.signals          = {}
        self.risk             = DailyRiskManager()

    def on_tick(self, ws, ticks):
        now = datetime.now(IST)
        session = get_current_session(now)

        for tick in ticks:
            token  = tick["instrument_token"]
            symbol = self.rev_tokens.get(token)
            if not symbol or symbol not in self.all_stocks: continue

            ltp    = tick.get("last_price", 0)
            volume = tick.get("volume_traded", 0)
            if ltp <= 0: continue

            # Update VWAP
            new_vol = max(0, volume - self.cum_vol[symbol])
            if new_vol > 0:
                self.cum_vol[symbol]    = volume
                self.cum_tp_vol[symbol] += ltp * new_vol
                if volume > 0:
                    self.vwap[symbol] = self.cum_tp_vol[symbol] / volume

            # Track recent prices for ATR
            self.prices[symbol].append(ltp)
            if len(self.prices[symbol]) > 50:
                self.prices[symbol] = self.prices[symbol][-50:]

            # Build Opening Range (9:15-9:30)
            if session == "opening":
                self.or_high[symbol] = max(self.or_high[symbol], ltp)
                self.or_low[symbol]  = min(self.or_low[symbol], ltp)
            elif session == "momentum" and not self.or_set[symbol]:
                # Opening range now set
                self.or_set[symbol] = True

            # Generate signals in valid sessions
            if session in ("momentum", "choppy", "afternoon"):
                self._check_signal(symbol, ltp, volume, now, session)

    def _compute_opportunity_score(self, symbol, ltp, volume, gap_pct, session) -> float:
        """Compute intraday opportunity score 0-100."""
        stock  = self.all_stocks.get(symbol, {})
        prev   = self.prev_close.get(symbol, ltp)
        vwap   = self.vwap.get(symbol, ltp)
        or_h   = self.or_high.get(symbol, ltp)
        or_l   = self.or_low.get(symbol, ltp)
        isin   = stock.get("isin", "")

        scores = {}

        # 1. Gap quality (0-100)
        abs_gap = abs(gap_pct)
        gap_score = min(abs_gap / 2.0, 1.0) * 100  # 2% gap = 100
        scores["gap"] = gap_score

        # 2. RVOL — relative volume vs daily average
        avg_daily_vol = self.rvol_baseline.get(isin, stock.get("avg_volume", 1))
        now = datetime.now(IST)
        mkt_min  = now.hour * 60 + now.minute - (9 * 60 + 15)
        frac_day = max(mkt_min / 375, 0.05)
        exp_vol  = avg_daily_vol * frac_day
        rvol     = volume / exp_vol if exp_vol > 0 else 1
        rvol_score = min((rvol - 1) / 3.0, 1.0) * 100  # 4x RVOL = 100
        scores["rvol"] = max(rvol_score, 0)

        # 3. VWAP position (0-100)
        above_vwap = ltp > vwap
        vwap_dist  = abs(ltp - vwap) / vwap * 100 if vwap > 0 else 0
        vwap_score = 80 if above_vwap else 20
        scores["vwap"] = vwap_score

        # 4. Opening Range Break (0-100)
        or_score = 0
        if self.or_set.get(symbol):
            or_range = or_h - or_l
            if or_range > 0:
                if ltp > or_h:   # Broke above OR
                    or_score = min((ltp - or_h) / or_range * 100, 100)
                elif ltp < or_l: # Broke below OR
                    or_score = min((or_l - ltp) / or_range * 100, 100)
        scores["orb"] = or_score

        # 5. Sector relative strength (proxy: momentum score as sector proxy for now)
        mom_score    = stock.get("momentum_score", 50)
        sector_score = mom_score  # Will be replaced with real sector RS later
        scores["sector"] = sector_score

        # 6. Market regime (0-100)
        regime_score = 70 if self.market_direction == "BULLISH" else 30 if self.market_direction == "BEARISH" else 50
        scores["regime"] = regime_score

        # 7. Key level distance (simplified — penalize if at resistance)
        # For now use distance from prev close as proxy
        dist_from_prev = abs(gap_pct)
        key_level_score = max(50 - dist_from_prev * 10, 0)  # Penalize huge gaps (exhaustion)
        scores["key_level"] = key_level_score

        # Session penalty
        session_mult = {"momentum": 1.0, "choppy": 0.7, "afternoon": 0.85}.get(session, 0.5)

        # Weighted opportunity score
        opp_score = (
            scores["gap"]       * 0.20 +
            scores["rvol"]      * 0.15 +
            scores["vwap"]      * 0.15 +
            scores["orb"]       * 0.15 +
            scores["sector"]    * 0.15 +
            scores["regime"]    * 0.10 +
            scores["key_level"] * 0.10
        ) * session_mult

        return round(opp_score, 2)

    def _check_signal(self, symbol, ltp, volume, now, session):
        if symbol in self.signals: return

        can_trade, reason = self.risk.can_trade()
        if not can_trade:
            return

        prev  = self.prev_close.get(symbol, 0)
        vwap  = self.vwap.get(symbol, ltp)
        stock = self.all_stocks.get(symbol, {})
        score = stock.get("investmitra_score", 50)
        if not prev: return

        gap_pct    = (ltp - prev) / prev * 100
        above_vwap = ltp > vwap * 1.001
        below_vwap = ltp < vwap * 0.999
        gap_thresh = GAP_THRESHOLDS.get(session, 0.4)

        # VIX adjustment
        if self.vix_signal == "ELEVATED": gap_thresh *= 1.5

        # Compute scores
        quality_score = stock.get("quality_score", 50)
        opp_score     = self._compute_opportunity_score(symbol, ltp, volume, gap_pct, session)
        final_score   = quality_score * 0.40 + opp_score * 0.60

        can_long  = self.market_direction in ("BULLISH", "NEUTRAL")
        can_short = self.market_direction in ("BEARISH", "NEUTRAL")

        # Minimum final score threshold
        if final_score < 45: return

        direction = None
        if symbol in self.long_map and can_long and gap_pct > gap_thresh and above_vwap and score >= 60:
            direction = "LONG"
        elif symbol in self.short_map and can_short and gap_pct < -gap_thresh and below_vwap and score <= 40:
            direction = "SHORT"

        if not direction: return

        # ATR-based stop and target
        atr = compute_atr(self.prices[symbol])
        if atr == 0: atr = ltp * 0.005  # fallback 0.5%

        if direction == "LONG":
            stop   = round(ltp - atr * ATR_STOP_MULTIPLIER, 2)
            target = round(ltp + atr * ATR_TARGET_MULTIPLIER, 2)
        else:
            stop   = round(ltp + atr * ATR_STOP_MULTIPLIER, 2)
            target = round(ltp - atr * ATR_TARGET_MULTIPLIER, 2)

        stop_dist = abs(ltp - stop)
        if stop_dist == 0: return

        # Position sizing
        position_size = int(MAX_RISK_PER_TRADE_INR / stop_dist)
        position_size = max(1, min(position_size, int(50000 / ltp)))  # max ₹50k exposure

        # Open position in risk manager
        self.risk.open_position(symbol, ltp, stop, position_size)

        self.signals[symbol] = dict(
            symbol=symbol, direction=direction, entry=ltp,
            target=target, stoploss=stop, atr=round(atr, 2),
            gap_pct=gap_pct, vwap=vwap, final_score=final_score,
            quality_score=quality_score, opp_score=opp_score,
            position_size=position_size,
            risk_inr=round(stop_dist * position_size, 0),
            session=session, time=now.strftime("%H:%M:%S"),
            screens=stock.get("screen_count",0), piotroski=stock.get("piotroski",0),
            cap=stock.get("cap","?"),
        )
        self._print_signal(self.signals[symbol], stock)

    def _print_signal(self, sig, stock):
        emoji  = "🟢 LONG " if sig["direction"] == "LONG" else "🔴 SHORT"
        pct    = abs(sig["entry"] - sig["target"]) / sig["entry"] * 100
        sl_pct = abs(sig["entry"] - sig["stoploss"]) / sig["entry"] * 100
        size_note = " | REDUCE SIZE" if self.vix_signal == "ELEVATED" else ""

        print(f"\n{'='*62}")
        print(f"  {emoji} SIGNAL — {sig['symbol']} [{sig['cap']}]{size_note}")
        print(f"  {stock.get('company_name','')[:45]}")
        print(f"{'='*62}")
        print(f"  Entry:         ₹{sig['entry']:,.2f}")
        print(f"  Target:        ₹{sig['target']:,.2f}  (+{pct:.1f}%)")
        print(f"  Stoploss:      ₹{sig['stoploss']:,.2f}  (-{sl_pct:.1f}%) [ATR: ₹{sig['atr']:.2f}]")
        print(f"  Position:      {sig['position_size']} shares  (Risk: ₹{sig['risk_inr']:.0f})")
        print(f"  Gap:           {sig['gap_pct']:+.2f}%")
        print(f"  VWAP:          ₹{sig['vwap']:,.2f}")
        print(f"  Final Score:   {sig['final_score']:.1f}  (Quality: {sig['quality_score']:.1f} | Opportunity: {sig['opp_score']:.1f})")
        print(f"  Screens:       {sig['screens']} | F-Score: {sig['piotroski']}")
        print(f"  Session:       {sig['session']} | Market: {self.market_direction}")
        print(f"  Time:          {sig['time']}")
        print(f"  ⚠️  Square off by 3:00 PM")
        print(f"{'='*62}\n")

        # Risk status
        print(f"  📊 Risk Status: Daily P&L ₹{self.risk.daily_pnl:.0f} | Trades: {self.risk.trades_today} | Positions: {len(self.risk.positions)}")

    def print_summary(self):
        longs  = [s for s in self.signals.values() if s["direction"] == "LONG"]
        shorts = [s for s in self.signals.values() if s["direction"] == "SHORT"]
        print(f"\n{'='*62}")
        print(f"  INTRADAY SUMMARY — {date.today()}")
        print(f"  Market: {self.market_direction} | VIX: {self.vix_signal}")
        print(f"  Daily P&L: ₹{self.risk.daily_pnl:.0f} | Trades: {self.risk.trades_today}")
        print(f"{'='*62}")
        for label, lst in [("🟢 LONG", longs), ("🔴 SHORT", shorts)]:
            if lst:
                print(f"\n  {label} ({len(lst)}):")
                for s in lst:
                    pnl_note = ""
                    print(f"    {s['symbol']:<15} Entry ₹{s['entry']:>8,.2f} → Target ₹{s['target']:>8,.2f} | SL ₹{s['stoploss']:,.2f} | {s['position_size']} shares{pnl_note}")
        if not longs and not shorts:
            print("  No signals triggered today.")
        print(f"\n  ⚠️  SQUARE OFF ALL POSITIONS BY 3:00 PM")
        print(f"{'='*62}\n")


def main():
    if not API_KEY or not ACCESS_TOKEN:
        print("❌ Run: python scripts/kite_login.py first")
        sys.exit(1)

    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(ACCESS_TOKEN)

    ctx              = get_premarket_context()
    market_direction, nifty_change = get_market_direction(kite, ctx)

    if ctx["vix_signal"] == "HIGH":
        print("🚨 VIX > 20 — Skipping intraday today.")
        sys.exit(0)

    rvol_baseline = get_historical_rvol_baseline()
    long_list, short_list = get_intraday_watchlist(ctx)

    if market_direction == "BULLISH":   short_list = []
    elif market_direction == "BEARISH": long_list  = []

    all_stocks = long_list + short_list
    if not all_stocks:
        logger.error("No stocks in watchlist")
        sys.exit(1)

    symbols   = list(set(s["symbol"] for s in all_stocks))
    token_map = get_instrument_tokens(kite, symbols)
    prev_close= get_previous_close(kite, list(token_map.keys()))

    # Print watchlist
    print(f"\n{'='*62}")
    print(f"  INTRADAY WATCHLIST — {date.today()} | {market_direction}")
    print(f"  Risk: ₹{MAX_RISK_PER_TRADE_INR}/trade | Daily limit: ₹{MAX_DAILY_LOSS_INR}")
    print(f"{'='*62}")
    if long_list:
        print(f"\n  🟢 LONG ({len(long_list)}) — Quality Score ranked:")
        print(f"  {'Symbol':<15} {'Quality':>8} {'Score':>7} {'Screens':>8} {'F-Score':>8}")
        print(f"  {'─'*55}")
        for s in long_list:
            if s["symbol"] in token_map:
                print(f"  {s['symbol']:<15} {s['quality_score']:>8.1f} {s['investmitra_score']:>7.1f} {s['screen_count']:>8} {s['piotroski']:>8}")
    if short_list:
        print(f"\n  🔴 SHORT ({len(short_list)}):")
        for s in short_list:
            if s["symbol"] in token_map:
                print(f"  {s['symbol']:<15} {s['quality_score']:>8.1f} {s['investmitra_score']:>7.1f}")
    print(f"\n  Sessions: 9:30-11:30 (momentum) | 11:30-1:30 (choppy-tighter) | 1:30-3:00 (afternoon)")
    print(f"  Signals use ATR-based stops + position sizing")
    print(f"{'='*62}\n")

    tokens = list(token_map.values())
    engine = IntradayEngine(long_list, short_list, token_map, prev_close,
                            market_direction, ctx, rvol_baseline)

    ticker = KiteTicker(API_KEY, ACCESS_TOKEN)
    ticker.on_connect = lambda ws, r: (ws.subscribe(tokens), ws.set_mode(ws.MODE_FULL, tokens))
    ticker.on_ticks   = engine.on_tick
    ticker.on_close   = lambda ws, c, r: logger.warning("Closed: %s", r)
    ticker.on_error   = lambda ws, c, r: logger.error("Error: %s", r)

    logger.info("Live feed starting — signals from 9:30 AM")
    try:
        ticker.connect(threaded=True)
        while True:
            now = datetime.now(IST)
            if now.hour >= 15 and now.minute >= 5:
                engine.print_summary()
                logger.info("Square off time — 3:00 PM reached")
                break
            time.sleep(10)
    except KeyboardInterrupt:
        engine.print_summary()


if __name__ == "__main__":
    main()
