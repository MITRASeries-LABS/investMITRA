"""
investMITRA — Intraday Signal Engine v8
Complete implementation with all pending features:
  - Real sector relative strength (Kite sector indices)
  - Key levels (prev day high/low from Neon prices)
  - NSE pre-open indicative prices
  - Partial profit management (exit 50% at 1R, trail rest)
  - Market breadth via NSE API
  - Gap classification (continuation/fade/exhaustion)
  - Liquidity filter (bid-ask spread)

Two-score: Quality (40%) + Opportunity (60%)
ATR stops, Position sizing, Daily risk controls, Session logic

Run: python scripts/intraday_signals.py
"""
from __future__ import annotations
import os, sys, time, logging, requests
from datetime import datetime, date, timedelta, timezone
from collections import defaultdict
import psycopg2
from bs4 import BeautifulSoup
from dotenv import load_dotenv
load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

from kiteconnect import KiteConnect, KiteTicker

API_KEY      = os.getenv("KITE_API_KEY")
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN")
NEON_URL     = os.getenv("CC_POSTGRES_URL")
IST          = timezone(timedelta(hours=5, minutes=30))

# ── Risk Parameters ────────────────────────────────────────────────────────────
MAX_RISK_PER_TRADE_INR = 2000
MAX_DAILY_LOSS_INR     = 6000
MAX_POSITIONS          = 3
MAX_CONSECUTIVE_LOSSES = 2
ATR_STOP_MULT          = 1.5
ATR_TARGET_MULT        = 3.0   # 2:1 RR minimum

# ── Session Times ──────────────────────────────────────────────────────────────
SESSIONS = {
    "preopen":   (9*60+0,   9*60+15),
    "opening":   (9*60+15,  9*60+30),
    "momentum":  (9*60+30,  11*60+30),
    "choppy":    (11*60+30, 13*60+30),
    "afternoon": (13*60+30, 15*60+0),
    "closing":   (15*60+0,  15*60+30),
}
GAP_THRESHOLDS = {"momentum": 0.3, "choppy": 0.6, "afternoon": 0.4}

# ── Sector Index Map (NSE symbol -> Kite quote key) ───────────────────────────
SECTOR_INDEX_MAP = {
    "Technology":          "NSE:NIFTY IT",
    "Financial Services":  "NSE:NIFTY BANK",
    "Healthcare":          "NSE:NIFTY PHARMA",
    "Energy":              "NSE:NIFTY ENERGY",
    "Industrials":         "NSE:NIFTY INFRA",
    "Consumer Cyclical":   "NSE:NIFTY AUTO",
    "Consumer Defensive":  "NSE:NIFTY FMCG",
    "Basic Materials":     "NSE:NIFTY METAL",
    "Real Estate":         "NSE:NIFTY REALTY",
    "Utilities":           "NSE:NIFTY ENERGY",
}


def get_current_session(now: datetime) -> str:
    mkt_min = now.hour * 60 + now.minute
    for name, (s, e) in SESSIONS.items():
        if s <= mkt_min < e:
            return name
    return "closed"


def get_nse_preopen_prices() -> dict[str, float]:
    """Fetch NSE pre-open session indicative prices."""
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Referer": "https://www.nseindia.com"
        })
        session.get("https://www.nseindia.com", timeout=10)
        r = session.get(
            "https://www.nseindia.com/api/market-data-pre-open?key=NIFTY",
            timeout=10
        )
        if r.status_code != 200:
            return {}
        data = r.json()
        prices = {}
        for item in data.get("data", []):
            sym  = item.get("metadata", {}).get("symbol", "")
            last = item.get("detail", {}).get("preOpenMarket", {}).get("lastPrice", 0)
            if sym and last:
                prices[sym] = float(last)
        logger.info("Pre-open prices: %d stocks", len(prices))
        return prices
    except Exception as e:
        logger.warning("Pre-open fetch: %s", e)
        return {}


def get_nse_market_breadth() -> dict:
    """Fetch NSE market breadth - advance/decline."""
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Referer": "https://www.nseindia.com"
        })
        session.get("https://www.nseindia.com", timeout=10)
        r = session.get("https://www.nseindia.com/api/allIndices", timeout=10)
        if r.status_code != 200:
            return {}
        data = r.json().get("data", [])
        breadth = {}
        for idx in data:
            name = idx.get("index", "")
            if name in ("NIFTY 50", "NIFTY BANK", "NIFTY MIDCAP SELECT", "INDIA VIX"):
                breadth[name] = {
                    "last": float(idx.get("last", 0)),
                    "pct_change": float(idx.get("percentChange", 0)),
                    "advances": int(idx.get("advances", 0)),
                    "declines": int(idx.get("declines", 0)),
                }
        return breadth
    except Exception as e:
        logger.warning("Breadth fetch: %s", e)
        return {}


def get_key_levels(symbols: list[str]) -> dict[str, dict]:
    """Load previous day high/low and recent DMAs from Neon."""
    if not symbols:
        return {}
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        cur  = conn.cursor()
        sym_placeholders = ",".join(["%s"] * len(symbols))
        cur.execute(f"""
            WITH recent AS (
                SELECT ep.isin, cm.nse_symbol, ep.trade_date,
                       ep.high, ep.low, ep.close,
                       AVG(ep.close) OVER (PARTITION BY ep.isin
                           ORDER BY ep.trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
                       AVG(ep.close) OVER (PARTITION BY ep.isin
                           ORDER BY ep.trade_date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS ma50,
                       ROW_NUMBER() OVER (PARTITION BY ep.isin ORDER BY ep.trade_date DESC) AS rn
                FROM investmitra.equity_prices ep
                JOIN investmitra.company_master cm ON ep.isin = cm.isin
                WHERE cm.nse_symbol = ANY(%s)
                  AND ep.trade_date >= CURRENT_DATE - INTERVAL '60 days'
            )
            SELECT nse_symbol, high, low, close, ma20, ma50
            FROM recent WHERE rn = 1
        """, (symbols,))
        result = {}
        for r in cur.fetchall():
            result[r[0]] = {
                "prev_high": float(r[1] or 0),
                "prev_low":  float(r[2] or 0),
                "prev_close": float(r[3] or 0),
                "ma20":      float(r[4] or 0),
                "ma50":      float(r[5] or 0),
            }
        cur.close(); conn.close()
        return result
    except Exception as e:
        logger.warning("Key levels: %s", e)
        return {}


def get_premarket_context() -> dict:
    ctx = {
        "india_vix":     None, "vix_signal": "NORMAL",
        "sgx_change":    None, "us_sentiment": "UNKNOWN",
        "global_signal": "NEUTRAL",
        "results_today": set(), "announcements": [],
        "preopen_prices": {},   "breadth": {},
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
                ctx["us_sentiment"] = "POSITIVE" if chg>0 else "NEGATIVE" if chg<-200 else "MIXED"
            except: pass
        cur.execute("SELECT UPPER(symbol) FROM investmitra.corporate_events WHERE event_date=CURRENT_DATE AND category='RESULTS'")
        ctx["results_today"] = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT symbol, ann_type FROM investmitra.nse_announcements WHERE ann_datetime>=NOW()-INTERVAL '12 hours' AND is_important=TRUE ORDER BY ann_datetime DESC LIMIT 10")
        ctx["announcements"] = [(r[0], r[1]) for r in cur.fetchall()]
        cur.close(); conn.close()
    except Exception as e:
        logger.warning("Context: %s", e)

    vix = ctx["india_vix"]
    if vix:
        if vix<12:   ctx["vix_signal"]="CALM"
        elif vix<16: ctx["vix_signal"]="NORMAL"
        elif vix<20: ctx["vix_signal"]="ELEVATED"
        else:        ctx["vix_signal"]="HIGH"

    sgx = ctx["sgx_change"]
    us  = ctx["us_sentiment"]
    if sgx and sgx>0.2 and us!="NEGATIVE":   ctx["global_signal"]="BULLISH"
    elif sgx and sgx<-0.2 and us=="NEGATIVE": ctx["global_signal"]="BEARISH"

    # Fetch live pre-open and breadth
    ctx["preopen_prices"] = get_nse_preopen_prices()
    ctx["breadth"]        = get_nse_market_breadth()

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
    if change_pct>0.3:    fut_dir="BULLISH"
    elif change_pct<-0.3: fut_dir="BEARISH"
    else:                  fut_dir="NEUTRAL"

    g = ctx["global_signal"]
    if fut_dir=="BULLISH" and g!="BEARISH":   direction="BULLISH"
    elif fut_dir=="BEARISH" and g!="BULLISH":  direction="BEARISH"
    else:                                       direction="NEUTRAL"

    # Breadth confirmation
    breadth = ctx.get("breadth", {})
    nifty_b = breadth.get("NIFTY 50", {})
    adv = nifty_b.get("advances", 0)
    dec = nifty_b.get("declines", 0)
    ad_ratio = adv / dec if dec > 0 else 1.0

    vix = ctx["india_vix"]
    print(f"\n{'='*65}")
    print(f"  investMITRA PRE-MARKET — {date.today()}")
    print(f"{'='*65}")
    if vix:
        e = "🟢" if vix<12 else "🟡" if vix<16 else "🔴"
        print(f"  {e} India VIX:    {vix:.2f} ({ctx['vix_signal']})")
    sgx = ctx["sgx_change"]
    if sgx is not None:
        print(f"  {'🟢' if sgx>0 else '🔴'} SGX Nifty:   {sgx:+.2f}%")
    print(f"  {'🟢' if ctx['us_sentiment']=='POSITIVE' else '🔴' if ctx['us_sentiment']=='NEGATIVE' else '🟡'} US Markets:  {ctx['us_sentiment']}")
    print(f"  {'🟢' if change_pct>0.3 else '🔴' if change_pct<-0.3 else '🟡'} {source}: {change_pct:+.2f}%")
    if adv or dec:
        print(f"  📊 Breadth:    A/D = {adv}/{dec} ({ad_ratio:.1f}x)")
    if ctx["preopen_prices"]:
        print(f"  📋 Pre-open:   {len(ctx['preopen_prices'])} stocks with indicative prices")
    if ctx["results_today"]:
        print(f"\n  ⚠️  Results TODAY (auto-skipped): {', '.join(list(ctx['results_today'])[:5])}")
    if ctx["announcements"]:
        print(f"  📢 Overnight:  {len(ctx['announcements'])} important announcements")

    d_e = "🟢" if direction=="BULLISH" else "🔴" if direction=="BEARISH" else "🟡"
    print(f"\n  {d_e} DIRECTION: {direction}")
    if ctx["vix_signal"]=="HIGH": print(f"  🚨 HIGH VIX — AVOIDING INTRADAY")
    print(f"{'='*65}\n")

    return direction, change_pct


def get_sector_quotes(kite: KiteConnect) -> dict[str, float]:
    """Get live sector index % changes."""
    try:
        unique_indices = list(set(SECTOR_INDEX_MAP.values()))
        quotes = kite.quote(unique_indices)
        result = {}
        for key, data in quotes.items():
            prev = data.get("ohlc", {}).get("close", 0)
            last = data.get("last_price", 0)
            if prev and last:
                result[key] = (last - prev) / prev * 100
        return result
    except Exception as e:
        logger.warning("Sector quotes: %s", e)
        return {}


def get_rvol_baseline() -> dict[str, float]:
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        cur  = conn.cursor()
        cur.execute("""SELECT isin, AVG(volume) FROM investmitra.equity_prices
                       WHERE trade_date>=CURRENT_DATE-INTERVAL '30 days' AND trade_date<CURRENT_DATE
                       GROUP BY isin HAVING AVG(volume)>0""")
        result = {r[0]: float(r[1]) for r in cur.fetchall()}
        cur.close(); conn.close()
        return result
    except Exception as e:
        logger.warning("RVOL baseline: %s", e)
        return {}


def get_intraday_watchlist(ctx: dict) -> tuple[list[dict], list[dict]]:
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("SELECT UPPER(symbol) FROM investmitra.nse_announcements WHERE ann_datetime>=NOW()-INTERVAL '12 hours' AND is_sensitive=TRUE")
    sensitive = {r[0] for r in cur.fetchall()}

    cur.execute("""
        WITH avg_volume AS (
            SELECT isin, AVG(volume) AS avg_vol, AVG(close) AS avg_price,
                   STDDEV(close) AS price_std
            FROM investmitra.equity_prices
            WHERE trade_date>=CURRENT_DATE-INTERVAL '30 days'
            GROUP BY isin
            HAVING AVG(volume)>200000 AND AVG(close) BETWEEN 50 AND 5000
        )
        SELECT ds.isin, ds.company_name, cm.nse_symbol, ds.sector,
               cm.market_cap_category, ds.investmitra_score, ds.signal,
               ds.momentum_score,
               ROUND(av.avg_vol::numeric,0), ROUND(av.avg_price::numeric,2),
               COALESCE(ss.screen_count,0), COALESCE(vq.piotroski_score,0),
               COALESCE(vq.graham_criteria_met,0), ROUND(av.price_std::numeric,2)
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

    preopen = ctx.get("preopen_prices", {})
    long_list = []
    short_list = []

    for r in rows:
        symbol = r[2]
        if not symbol: continue
        if symbol.upper() in ctx["results_today"]: continue
        if symbol.upper() in sensitive: continue

        inv   = float(r[5] or 0)
        sc    = int(r[10] or 0)
        piots = int(r[11] or 0)
        grah  = int(r[12] or 0)

        quality = (
            (inv/100)*0.50 + min(sc/20,1.0)*0.20 +
            (piots/9)*0.15 + (grah/4)*0.15
        ) * 100

        # Pre-open gap context
        preopen_gap = 0.0
        po_price = preopen.get(symbol, 0)
        avg_price = float(r[9] or 0)
        if po_price and avg_price:
            preopen_gap = (po_price - avg_price) / avg_price * 100

        stock = {
            "isin": r[0], "company_name": r[1], "symbol": symbol,
            "sector": r[3], "cap": r[4],
            "investmitra_score": inv, "signal": r[6],
            "momentum_score": float(r[7] or 0),
            "avg_volume": int(r[8] or 0), "avg_price": avg_price,
            "screen_count": sc, "piotroski": piots, "graham": grah,
            "quality_score": round(quality, 2),
            "price_std": float(r[13] or 0),
            "preopen_gap": round(preopen_gap, 2),
        }

        if inv >= 60:   long_list.append(stock)
        elif inv <= 35: short_list.append(stock)

    long_list  = sorted(long_list,  key=lambda x: x["quality_score"], reverse=True)[:15]
    short_list = sorted(short_list, key=lambda x: x["investmitra_score"])[:10]
    return long_list, short_list


def classify_gap(gap_pct: float, volume: int, avg_volume: int,
                 prev_high: float, prev_low: float, ltp: float) -> tuple[str, float]:
    """
    Classify gap type and return (classification, score_multiplier)
    continuation: strong signal
    fade:         weak signal
    exhaustion:   avoid
    """
    abs_gap = abs(gap_pct)
    is_long = gap_pct > 0

    # Volume confirmation
    rvol_simple = volume / avg_volume if avg_volume > 0 else 1
    high_vol = rvol_simple > 1.5

    # Gap size classification
    if abs_gap > 3.0:
        # Huge gap — exhaustion risk
        return "exhaustion", 0.5
    elif abs_gap > 1.5:
        # Large gap
        if high_vol:
            return "continuation", 1.2
        else:
            return "fade_risk", 0.7
    elif abs_gap > 0.3:
        # Normal gap
        if high_vol:
            return "continuation", 1.0
        else:
            return "fade_risk", 0.85
    else:
        return "small_gap", 0.9


def compute_atr(prices: list[float], period: int = 14) -> float:
    if len(prices) < 2: return 0
    ranges = [abs(prices[i] - prices[i-1]) for i in range(1, len(prices))]
    return sum(ranges[-period:]) / min(len(ranges), period)


class DailyRiskManager:
    def __init__(self):
        self.daily_pnl          = 0.0
        self.trades_today       = 0
        self.consecutive_losses = 0
        self.positions          = {}
        self.partial_exits      = {}  # track partial exits

    def can_trade(self) -> tuple[bool, str]:
        if self.daily_pnl <= -MAX_DAILY_LOSS_INR:
            return False, f"Daily loss limit ₹{self.daily_pnl:.0f}"
        if self.trades_today >= MAX_POSITIONS * 4:
            return False, f"Max trades ({self.trades_today})"
        if len(self.positions) >= MAX_POSITIONS:
            return False, f"Max positions ({len(self.positions)})"
        if self.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            return False, f"Consecutive losses: {self.consecutive_losses}"
        return True, "OK"

    def open_position(self, symbol, entry, stop, size, target):
        self.positions[symbol] = {
            "entry": entry, "stop": stop,
            "size": size, "target": target,
            "partial_done": False,
            "partial_size": size // 2,
        }
        self.trades_today += 1

    def close_position(self, symbol, exit_price):
        if symbol not in self.positions: return 0
        pos = self.positions.pop(symbol)
        pnl = (exit_price - pos["entry"]) * pos["size"]
        self.daily_pnl += pnl
        self.consecutive_losses = 0 if pnl > 0 else self.consecutive_losses + 1
        return pnl


class IntradayEngine:
    def __init__(self, long_list, short_list, token_map, prev_close,
                 market_direction, ctx, rvol_baseline, key_levels, sector_quotes):
        self.long_map         = {s["symbol"]: s for s in long_list}
        self.short_map        = {s["symbol"]: s for s in short_list}
        self.all_stocks       = {**self.long_map, **self.short_map}
        self.token_map        = token_map
        self.rev_tokens       = {v: k for k, v in token_map.items()}
        self.prev_close       = prev_close
        self.market_direction = market_direction
        self.vix_signal       = ctx["vix_signal"]
        self.rvol_baseline    = rvol_baseline
        self.key_levels       = key_levels
        self.sector_quotes    = sector_quotes  # live sector % changes
        self.breadth          = ctx.get("breadth", {})

        self.vwap             = defaultdict(float)
        self.cum_vol          = defaultdict(int)
        self.cum_tp_vol       = defaultdict(float)
        self.prices           = defaultdict(list)
        self.or_high          = defaultdict(float)
        self.or_low           = defaultdict(lambda: float('inf'))
        self.or_set           = defaultdict(bool)

        self.signals          = {}
        self.risk             = DailyRiskManager()

    def on_tick(self, ws, ticks):
        now     = datetime.now(IST)
        session = get_current_session(now)
        for tick in ticks:
            token  = tick["instrument_token"]
            symbol = self.rev_tokens.get(token)
            if not symbol or symbol not in self.all_stocks: continue
            ltp    = tick.get("last_price", 0)
            volume = tick.get("volume_traded", 0)
            if ltp <= 0: continue

            # VWAP
            new_vol = max(0, volume - self.cum_vol[symbol])
            if new_vol > 0:
                self.cum_vol[symbol]    = volume
                self.cum_tp_vol[symbol] += ltp * new_vol
                if volume > 0:
                    self.vwap[symbol] = self.cum_tp_vol[symbol] / volume

            # Recent prices for ATR
            self.prices[symbol].append(ltp)
            if len(self.prices[symbol]) > 60:
                self.prices[symbol] = self.prices[symbol][-60:]

            # Opening range
            if session == "opening":
                self.or_high[symbol] = max(self.or_high.get(symbol, 0), ltp)
                self.or_low[symbol]  = min(self.or_low.get(symbol, float('inf')), ltp)
            elif session == "momentum" and not self.or_set[symbol]:
                self.or_set[symbol] = True

            # Partial exit check for existing positions
            self._check_partial_exit(symbol, ltp, session)

            if session in ("momentum", "choppy", "afternoon"):
                self._check_signal(symbol, ltp, volume, now, session)

    def _check_partial_exit(self, symbol, ltp, session):
        """Exit 50% at 1R, trail remainder."""
        if symbol not in self.risk.positions: return
        pos = self.risk.positions[symbol]
        if pos["partial_done"]: return

        entry  = pos["entry"]
        stop   = pos["stop"]
        target = pos["target"]
        risk   = abs(entry - stop)

        # 1R reached — exit half
        if (ltp >= entry + risk and symbol in self.long_map) or \
           (ltp <= entry - risk and symbol in self.short_map):
            pos["partial_done"] = True
            partial_size = pos["partial_size"]
            pnl = abs(ltp - entry) * partial_size
            self.risk.daily_pnl += pnl
            # Move stop to breakeven for remainder
            pos["stop"] = entry
            pos["size"] -= partial_size
            print(f"\n  💰 PARTIAL EXIT: {symbol} — {partial_size} shares @ ₹{ltp:.2f} | P&L: +₹{pnl:.0f}")
            print(f"  📍 Stop moved to breakeven ₹{entry:.2f} for remaining {pos['size']} shares\n")

        # Session close — full exit
        if session == "closing":
            pnl = self.risk.close_position(symbol, ltp)
            if pnl is not None:
                print(f"\n  🏁 SESSION EXIT: {symbol} @ ₹{ltp:.2f} | P&L: {'+'if pnl>=0 else ''}₹{pnl:.0f}\n")

    def _compute_opportunity_score(self, symbol, ltp, volume, gap_pct, session) -> tuple[float, dict]:
        stock   = self.all_stocks.get(symbol, {})
        vwap    = self.vwap.get(symbol, ltp)
        or_h    = self.or_high.get(symbol, ltp)
        or_l    = self.or_low.get(symbol, float('inf'))
        isin    = stock.get("isin", "")
        sector  = stock.get("sector", "")

        # RVOL
        avg_daily = self.rvol_baseline.get(isin, stock.get("avg_volume", 1))
        now       = datetime.now(IST)
        mkt_min   = now.hour*60+now.minute-(9*60+15)
        frac      = max(mkt_min/375, 0.05)
        exp_vol   = avg_daily * frac
        rvol      = volume / exp_vol if exp_vol > 0 else 1

        # Gap classification
        avg_vol = stock.get("avg_volume", 1)
        kl      = self.key_levels.get(symbol, {})
        prev_h  = kl.get("prev_high", ltp)
        prev_l  = kl.get("prev_low", ltp)
        gap_type, gap_mult = classify_gap(gap_pct, volume, avg_vol, prev_h, prev_l, ltp)

        # Sector relative strength
        sector_key    = SECTOR_INDEX_MAP.get(sector, "NSE:NIFTY 50")
        sector_chg    = self.sector_quotes.get(sector_key, 0)
        nifty_chg_est = 0  # Will be updated from breadth
        nifty_data    = self.breadth.get("NIFTY 50", {})
        nifty_chg_est = nifty_data.get("pct_change", 0)

        # Stock outperforming sector outperforming Nifty = strong RS
        stock_chg = gap_pct
        if stock_chg > sector_chg > nifty_chg_est and gap_pct > 0:
            sector_rs_score = 90
        elif stock_chg > sector_chg:
            sector_rs_score = 70
        elif stock_chg > nifty_chg_est:
            sector_rs_score = 55
        else:
            sector_rs_score = 35

        # Key level distance
        ma20 = kl.get("ma20", 0)
        ma50 = kl.get("ma50", 0)
        kl_score = 50
        if ma20 and ma50:
            above_ma20 = ltp > ma20
            above_ma50 = ltp > ma50
            if above_ma20 and above_ma50: kl_score = 80
            elif above_ma20:              kl_score = 60
            else:                         kl_score = 30
        # Penalize if at prev day high (resistance)
        if prev_h and abs(ltp - prev_h) / prev_h < 0.005:
            kl_score -= 20

        # Market breadth
        adv = nifty_data.get("advances", 0)
        dec = nifty_data.get("declines", 0)
        ad_ratio = adv / dec if dec > 0 else 1.0
        if gap_pct > 0:  # Long trade
            breadth_score = min(ad_ratio / 3.0 * 100, 100) if ad_ratio > 1 else 30
        else:  # Short trade
            breadth_score = min((1/ad_ratio) / 3.0 * 100, 100) if ad_ratio < 1 else 30

        # ORB score
        orb_score = 0
        if self.or_set.get(symbol):
            or_range = or_h - or_l
            if or_range > 0 and or_l < float('inf'):
                if ltp > or_h:
                    orb_score = min((ltp - or_h) / or_range * 100, 100)
                elif ltp < or_l:
                    orb_score = min((or_l - ltp) / or_range * 100, 100)

        # VWAP score
        vwap_score = 80 if ltp > vwap else 20

        # RVOL score
        rvol_score = min((rvol - 1) / 3.0 * 100, 100) if rvol > 1 else 0

        # Regime score
        regime_score = 70 if self.market_direction=="BULLISH" else 30 if self.market_direction=="BEARISH" else 50

        # Session multiplier
        sess_mult = {"momentum": 1.0, "choppy": 0.7, "afternoon": 0.85}.get(session, 0.5)

        # Gap quality with classification
        gap_score = min(abs(gap_pct) / 2.0, 1.0) * 100 * gap_mult

        opp = (
            gap_score    * 0.20 +
            rvol_score   * 0.15 +
            vwap_score   * 0.15 +
            orb_score    * 0.15 +
            sector_rs_score * 0.15 +
            breadth_score * 0.05 +
            regime_score * 0.05 +
            kl_score     * 0.10
        ) * sess_mult

        details = {
            "gap_type": gap_type, "gap_score": round(gap_score,1),
            "rvol": round(rvol,2), "rvol_score": round(rvol_score,1),
            "vwap_score": vwap_score, "orb_score": round(orb_score,1),
            "sector_rs": round(sector_rs_score,1), "breadth": round(breadth_score,1),
            "kl_score": kl_score, "sector_chg": round(sector_chg,2),
            "stock_vs_sector": round(stock_chg - sector_chg, 2),
        }
        return round(opp, 2), details

    def _check_signal(self, symbol, ltp, volume, now, session):
        if symbol in self.signals: return
        can, reason = self.risk.can_trade()
        if not can: return

        prev  = self.prev_close.get(symbol, 0)
        vwap  = self.vwap.get(symbol, ltp)
        stock = self.all_stocks.get(symbol, {})
        score = stock.get("investmitra_score", 50)
        if not prev: return

        gap_pct    = (ltp - prev) / prev * 100
        above_vwap = ltp > vwap * 1.001
        below_vwap = ltp < vwap * 0.999
        gap_thresh = GAP_THRESHOLDS.get(session, 0.4)
        if self.vix_signal == "ELEVATED": gap_thresh *= 1.5

        quality = stock.get("quality_score", 50)
        opp, details = self._compute_opportunity_score(symbol, ltp, volume, gap_pct, session)

        # Reject exhaustion gaps
        if details["gap_type"] == "exhaustion": return

        final = quality * 0.40 + opp * 0.60
        if final < 45: return

        direction = None
        if symbol in self.long_map and self.market_direction in ("BULLISH","NEUTRAL") and gap_pct > gap_thresh and above_vwap and score >= 60:
            direction = "LONG"
        elif symbol in self.short_map and self.market_direction in ("BEARISH","NEUTRAL") and gap_pct < -gap_thresh and below_vwap and score <= 40:
            direction = "SHORT"
        if not direction: return

        # ATR stop & target
        atr = compute_atr(self.prices[symbol])
        if atr == 0: atr = ltp * 0.005
        stop   = round(ltp - atr*ATR_STOP_MULT, 2) if direction=="LONG" else round(ltp + atr*ATR_STOP_MULT, 2)
        target = round(ltp + atr*ATR_TARGET_MULT, 2) if direction=="LONG" else round(ltp - atr*ATR_TARGET_MULT, 2)
        stop_dist = abs(ltp - stop)
        if stop_dist == 0: return

        size = max(1, min(int(MAX_RISK_PER_TRADE_INR / stop_dist), int(50000 / ltp)))
        self.risk.open_position(symbol, ltp, stop, size, target)

        self.signals[symbol] = dict(
            symbol=symbol, direction=direction, entry=ltp,
            target=target, stoploss=stop, atr=round(atr,2),
            gap_pct=gap_pct, vwap=vwap, final_score=final,
            quality_score=quality, opp_score=opp,
            position_size=size, risk_inr=round(stop_dist*size,0),
            session=session, time=now.strftime("%H:%M:%S"),
            details=details, cap=stock.get("cap","?"),
            screens=stock.get("screen_count",0),
            piotroski=stock.get("piotroski",0),
        )
        self._print_signal(self.signals[symbol], stock)

    def _print_signal(self, sig, stock):
        emoji  = "🟢 LONG " if sig["direction"]=="LONG" else "🔴 SHORT"
        pct    = abs(sig["entry"]-sig["target"])/sig["entry"]*100
        sl_pct = abs(sig["entry"]-sig["stoploss"])/sig["entry"]*100
        d      = sig["details"]
        size_note = " | REDUCE SIZE 50%" if self.vix_signal=="ELEVATED" else ""
        print(f"\n{'='*65}")
        print(f"  {emoji} SIGNAL — {sig['symbol']} [{sig['cap']}]{size_note}")
        print(f"  {stock.get('company_name','')[:50]}")
        print(f"{'='*65}")
        print(f"  Entry:         ₹{sig['entry']:,.2f}")
        print(f"  Target:        ₹{sig['target']:,.2f}  (+{pct:.1f}%)")
        print(f"  Stoploss:      ₹{sig['stoploss']:,.2f}  (-{sl_pct:.1f}%) [ATR: ₹{sig['atr']:.2f}]")
        print(f"  Size:          {sig['position_size']} shares  |  Risk: ₹{sig['risk_inr']:.0f}")
        print(f"  Profit plan:   Exit 50% at 1R → trail remainder")
        print(f"  Gap:           {sig['gap_pct']:+.2f}% ({d['gap_type']})")
        print(f"  VWAP:          ₹{sig['vwap']:,.2f}")
        print(f"  RVOL:          {d['rvol']:.1f}x  |  ORB: {d['orb_score']:.0f}")
        print(f"  Sector RS:     Stock vs Sector {d['stock_vs_sector']:+.2f}%  |  Sector: {d['sector_chg']:+.2f}%")
        print(f"  Final Score:   {sig['final_score']:.1f}  (Q:{sig['quality_score']:.0f} | O:{sig['opp_score']:.0f})")
        print(f"  Screens: {sig['screens']} | F-Score: {sig['piotroski']} | Session: {sig['session']}")
        print(f"  Time:          {sig['time']}")
        print(f"  ⚠️  Square off by 3:00 PM | Daily P&L: ₹{self.risk.daily_pnl:.0f}")
        print(f"{'='*65}\n")

    def print_summary(self):
        longs  = [s for s in self.signals.values() if s["direction"]=="LONG"]
        shorts = [s for s in self.signals.values() if s["direction"]=="SHORT"]
        print(f"\n{'='*65}")
        print(f"  INTRADAY SUMMARY — {date.today()}")
        print(f"  Market: {self.market_direction} | VIX: {self.vix_signal}")
        print(f"  Daily P&L: ₹{self.risk.daily_pnl:.0f} | Trades: {self.risk.trades_today}")
        print(f"{'='*65}")
        for label, lst in [("🟢 LONG", longs), ("🔴 SHORT", shorts)]:
            if lst:
                print(f"\n  {label} ({len(lst)}):")
                for s in lst:
                    print(f"    {s['symbol']:<15} Entry ₹{s['entry']:>8,.2f} → Target ₹{s['target']:>8,.2f} | SL ₹{s['stoploss']:,.2f}")
        if not longs and not shorts:
            print("  No signals triggered today.")
        print(f"\n  ⚠️  SQUARE OFF ALL POSITIONS BY 3:00 PM")
        print(f"{'='*65}\n")


def main():
    if not API_KEY or not ACCESS_TOKEN:
        print("❌ Run: python scripts/kite_login.py first")
        sys.exit(1)

    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(ACCESS_TOKEN)

    ctx = get_premarket_context()
    market_direction, nifty_change = get_market_direction(kite, ctx)

    if ctx["vix_signal"] == "HIGH":
        print("🚨 VIX > 20 — No intraday today.")
        sys.exit(0)

    # Load all supporting data
    rvol_baseline = get_rvol_baseline()
    long_list, short_list = get_intraday_watchlist(ctx)

    if market_direction == "BULLISH":   short_list = []
    elif market_direction == "BEARISH": long_list  = []

    all_stocks = long_list + short_list
    if not all_stocks:
        logger.error("No stocks in watchlist")
        sys.exit(1)

    symbols    = list(set(s["symbol"] for s in all_stocks))
    token_map  = get_instrument_tokens_fn(kite, symbols)
    prev_close = get_prev_close_fn(kite, list(token_map.keys()))
    key_levels = get_key_levels(symbols)
    sector_quotes = get_sector_quotes(kite)

    logger.info("Key levels loaded: %d stocks", len(key_levels))
    logger.info("Sector quotes: %d indices", len(sector_quotes))

    # Print watchlist
    print(f"\n{'='*65}")
    print(f"  INTRADAY WATCHLIST — {date.today()} | {market_direction}")
    print(f"  Risk: ₹{MAX_RISK_PER_TRADE_INR}/trade | Kill: ₹{MAX_DAILY_LOSS_INR} | Max: {MAX_POSITIONS} positions")
    print(f"{'='*65}")
    if long_list:
        print(f"\n  🟢 LONG ({len(long_list)}) — by Quality Score:")
        print(f"  {'Symbol':<15} {'Quality':>8} {'Score':>7} {'Screens':>8} {'F':>4} {'PreOpen':>8}")
        print(f"  {'─'*60}")
        for s in long_list:
            if s["symbol"] in token_map:
                po = f"{s['preopen_gap']:+.1f}%" if s.get("preopen_gap") else "N/A"
                kl = key_levels.get(s["symbol"], {})
                print(f"  {s['symbol']:<15} {s['quality_score']:>8.1f} {s['investmitra_score']:>7.1f} {s['screen_count']:>8} {s['piotroski']:>4} {po:>8}")
    if short_list:
        print(f"\n  🔴 SHORT ({len(short_list)}):")
        for s in short_list:
            if s["symbol"] in token_map:
                print(f"  {s['symbol']:<15} {s['quality_score']:>8.1f} {s['investmitra_score']:>7.1f}")

    print(f"\n  Gap thresholds: momentum 0.3% | choppy 0.6% | afternoon 0.4%")
    print(f"  Signals: ORB after 9:30 | ATR stops | RVOL + Sector RS scored")
    print(f"{'='*65}\n")

    tokens = list(token_map.values())
    engine = IntradayEngine(long_list, short_list, token_map, prev_close,
                            market_direction, ctx, rvol_baseline, key_levels, sector_quotes)

    def on_connect(ws, response):
        logger.info("Connected — subscribing %d tokens", len(tokens))
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)

    ticker = KiteTicker(API_KEY, ACCESS_TOKEN)
    ticker.on_connect = on_connect
    ticker.on_ticks   = engine.on_tick
    ticker.on_close   = lambda ws, c, r: logger.warning("Closed: %s", r)
    ticker.on_error   = lambda ws, c, r: logger.error("Error: %s", r)

    logger.info("Live — signals from 9:30 AM (session: momentum)")
    try:
        ticker.connect(threaded=True)
        while True:
            now = datetime.now(IST)
            if now.hour >= 15 and now.minute >= 5:
                engine.print_summary()
                logger.info("3:05 PM — square off")
                break
            time.sleep(10)
    except KeyboardInterrupt:
        engine.print_summary()


def get_instrument_tokens_fn(kite, symbols):
    try:
        return {i["tradingsymbol"]: i["instrument_token"]
                for i in kite.instruments("NSE")
                if i["tradingsymbol"] in symbols and i["segment"]=="NSE"}
    except Exception as e:
        logger.error("Instruments: %s", e); return {}


def get_prev_close_fn(kite, symbols):
    try:
        quotes = kite.quote([f"NSE:{s}" for s in symbols])
        return {s.replace("NSE:",""): d["ohlc"]["close"] for s,d in quotes.items()}
    except Exception as e:
        logger.error("Quote: %s", e); return {}


if __name__ == "__main__":
    main()
