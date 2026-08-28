"""
investMITRA — Intraday Signal Engine v10 Final
All safeguards + expanded universe + gap quality filters

KEY CHANGES from v9:
  1. TRUE GAP = today's open vs prev close (not current price)
  2. Gap hold confirmation: gap must hold 5 min (not one tick)
  3. Traded value filter for all caps (replaces volume-only)
     MID/LARGE: min ₹5Cr daily | SMALL/MICRO: min ₹50L daily
  4. Price cap raised to ₹20,000 (catches APARINDS, AIAENG)
  5. Exclude stocks up/down >5% prev day (chasing/exhaustion)
  6. Exclude stocks with results in next 3 days (not just today)
  7. Trailing stop: moves up by ATR/2 after each 1R gain
  8. Dead trade exit: if no movement 45 min after signal → exit
  9. Gap reversal exit: if price crosses back through open → exit
  10. NSE bulk deals as additional gap signal source
  11. 52-week high/low proximity scoring
  12. Pre-open equilibrium as gap conviction boost
"""
from __future__ import annotations
import os, sys, time, logging, requests
from datetime import datetime, date, timedelta, timezone
from collections import defaultdict
import psycopg2
from dotenv import load_dotenv
load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

class _WSFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        if 'uncleanly' in msg: return False
        if 'peer dropped' in msg: return False
        if 'WebSocket closing handshake' in msg: return False
        if 'Connection error: 1006' in msg: return False
        if 'Connection closed: 1006' in msg: return False
        return True

logging.getLogger().addFilter(_WSFilter())
# Suppress noisy WebSocket disconnect messages
import logging as _logging
class _WSFilter(_logging.Filter):
    def filter(self, record):
        msg = str(record.getMessage())
        if 'connection was closed uncleanly' in msg: return False
        if 'peer dropped the TCP' in msg: return False
        if 'WebSocket closing handshake' in msg: return False
        return True
_logging.getLogger().addFilter(_WSFilter())
logger = logging.getLogger(__name__)

from kiteconnect import KiteConnect, KiteTicker

API_KEY      = os.getenv("KITE_API_KEY")
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN")
NEON_URL     = os.getenv("CC_POSTGRES_URL")
IST          = timezone(timedelta(hours=5, minutes=30))

# ── Risk Parameters ────────────────────────────────────────────────────────────
MAX_RISK_PER_TRADE_INR  = 2000
MAX_CAPITAL_PER_TRADE   = 25000
MAX_DAILY_LOSS_INR      = 6000
MAX_POSITIONS           = 3
MAX_CONSECUTIVE_LOSSES  = 2
ATR_STOP_MULT           = 1.5
ATR_TARGET_MULT         = 1.5
BROKERAGE_PER_TRADE     = 80
MIN_NET_PROFIT          = 200
GAP_HOLD_MINUTES        = 5     # Gap must hold for 5 min before signal
DEAD_TRADE_MINUTES      = 40    # Exit if no movement after 40 min

# ── Session Times ──────────────────────────────────────────────────────────────
SESSIONS = {
    "preopen":   (9*60+0,   9*60+15),
    "opening":   (9*60+15,  9*60+30),
    "momentum":  (9*60+30,  11*60+30),
    "choppy":    (11*60+30, 13*60+30),
    "afternoon": (13*60+30, 15*60+0),
    "closing":   (15*60+0,  15*60+30),
}
GAP_THRESHOLDS = {"momentum": 0.3, "choppy": 0.6, "afternoon": 0.4}  # defaults

def load_signal_weights() -> dict:
    """Load latest signal weights from Neon (updated by weekly Opus review)."""
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        cur  = conn.cursor()
        cur.execute("""
            SELECT weights FROM investmitra.signal_weights
            WHERE effective_date <= CURRENT_DATE
            ORDER BY effective_date DESC LIMIT 1
        """)
        row = cur.fetchone()
        cur.close(); conn.close()
        if row:
            import json
            w = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            logger.info("Loaded signal weights effective %s", w.get("effective_date","?"))
            return w
    except Exception as e:
        logger.warning("Load weights failed: %s ? using defaults", e)
    return {}

SECTOR_INDEX_MAP = {
    "Technology":         "NSE:NIFTY IT",
    "Financial Services": "NSE:NIFTY BANK",
    "Healthcare":         "NSE:NIFTY PHARMA",
    "Energy":             "NSE:NIFTY ENERGY",
    "Industrials":        "NSE:NIFTY INFRA",
    "Consumer Cyclical":  "NSE:NIFTY AUTO",
    "Consumer Defensive": "NSE:NIFTY FMCG",
    "Basic Materials":    "NSE:NIFTY METAL",
    "Real Estate":        "NSE:NIFTY REALTY",
    "Utilities":          "NSE:NIFTY ENERGY",
}


def get_current_session(now: datetime) -> str:
    mkt_min = now.hour * 60 + now.minute
    for name, (s, e) in SESSIONS.items():
        if s <= mkt_min < e:
            return name
    return "closed"


def get_nse_bulk_deals() -> set[str]:
    """Fetch yesterday's NSE bulk deals — stocks with institutional activity."""
    try:
        nse = requests.Session()
        nse.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Referer": "https://www.nseindia.com"})
        nse.get("https://www.nseindia.com", timeout=10)
        r = nse.get("https://www.nseindia.com/api/bulk-deals", timeout=10)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                symbols = {item.get("symbol","").upper() for item in data}
                logger.info("Bulk deals: %d stocks", len(symbols))
                return symbols
    except Exception as e:
        logger.warning("Bulk deals: %s", e)
    return set()


def get_nse_preopen_prices() -> dict[str, float]:
    try:
        nse = requests.Session()
        nse.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Referer": "https://www.nseindia.com"})
        nse.get("https://www.nseindia.com", timeout=10)
        r = nse.get("https://www.nseindia.com/api/market-data-pre-open?key=NIFTY", timeout=10)
        if r.status_code != 200: return {}
        prices = {}
        for item in r.json().get("data", []):
            sym  = item.get("metadata", {}).get("symbol", "")
            last = item.get("detail", {}).get("preOpenMarket", {}).get("lastPrice", 0)
            if sym and last: prices[sym] = float(last)
        logger.info("Pre-open: %d stocks", len(prices))
        return prices
    except Exception as e:
        logger.warning("Pre-open: %s", e); return {}


def get_nse_market_breadth() -> dict:
    try:
        nse = requests.Session()
        nse.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Referer": "https://www.nseindia.com"})
        nse.get("https://www.nseindia.com", timeout=10)
        r = nse.get("https://www.nseindia.com/api/allIndices", timeout=10)
        if r.status_code != 200: return {}
        breadth = {}
        for idx in r.json().get("data", []):
            name = idx.get("index", "")
            if name in ("NIFTY 50", "NIFTY BANK", "NIFTY MIDCAP SELECT", "INDIA VIX"):
                breadth[name] = {
                    "last":       float(idx.get("last", 0)),
                    "pct_change": float(idx.get("percentChange", 0)),
                    "advances":   int(idx.get("advances", 0)),
                    "declines":   int(idx.get("declines", 0)),
                }
        return breadth
    except Exception as e:
        logger.warning("Breadth: %s", e); return {}


def get_key_levels(symbols: list[str]) -> dict[str, dict]:
    if not symbols: return {}
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        cur  = conn.cursor()
        cur.execute("""
            WITH recent AS (
                SELECT ep.isin, cm.nse_symbol, ep.trade_date,
                       ep.open, ep.high, ep.low, ep.close,
                       ep.high - ep.low AS daily_range,
                       AVG(ep.close) OVER (PARTITION BY ep.isin ORDER BY ep.trade_date
                           ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
                       AVG(ep.close) OVER (PARTITION BY ep.isin ORDER BY ep.trade_date
                           ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS ma50,
                       AVG(ep.high - ep.low) OVER (PARTITION BY ep.isin ORDER BY ep.trade_date
                           ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS atr14,
                       MAX(ep.high) OVER (PARTITION BY ep.isin ORDER BY ep.trade_date
                           ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS high_52w,
                       MIN(ep.low) OVER (PARTITION BY ep.isin ORDER BY ep.trade_date
                           ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS low_52w,
                       (ep.close - LAG(ep.close) OVER (PARTITION BY ep.isin ORDER BY ep.trade_date))
                           / NULLIF(LAG(ep.close) OVER (PARTITION BY ep.isin ORDER BY ep.trade_date), 0) * 100
                           AS prev_day_chg_pct,
                       ROW_NUMBER() OVER (PARTITION BY ep.isin ORDER BY ep.trade_date DESC) AS rn
                FROM investmitra.equity_prices ep
                JOIN investmitra.company_master cm ON ep.isin = cm.isin
                WHERE cm.nse_symbol = ANY(%s)
                  AND ep.trade_date >= CURRENT_DATE - INTERVAL '60 days'
            )
            SELECT nse_symbol, open, high, low, close, ma20, ma50,
                   atr14, daily_range, high_52w, low_52w, prev_day_chg_pct
            FROM recent WHERE rn = 1
        """, (symbols,))
        result = {}
        for r in cur.fetchall():
            result[r[0]] = {
                "prev_open":       float(r[1] or 0),
                "prev_high":       float(r[2] or 0),
                "prev_low":        float(r[3] or 0),
                "prev_close":      float(r[4] or 0),
                "ma20":            float(r[5] or 0),
                "ma50":            float(r[6] or 0),
                "atr14":           float(r[7] or 0),
                "daily_range":     float(r[8] or 0),
                "high_52w":        float(r[9] or 0),
                "low_52w":         float(r[10] or 0),
                "prev_day_chg":    float(r[11] or 0),
            }
        cur.close(); conn.close()
        return result
    except Exception as e:
        logger.warning("Key levels: %s", e); return {}


def get_stock_sentiment(symbols: list[str]) -> dict[str, float]:
    if not symbols: return {}
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        cur  = conn.cursor()
        cur.execute("""
            SELECT cm.nse_symbol, AVG(ne.sentiment_score)
            FROM investmitra.news_events ne
            JOIN investmitra.company_master cm ON cm.isin = ANY(ne.entities_isin)
            WHERE ne.sentiment_score IS NOT NULL
              AND ne.published_at >= NOW() - INTERVAL '7 days'
              AND cm.nse_symbol = ANY(%s)
            GROUP BY cm.nse_symbol
        """, (symbols,))
        result = {r[0]: float(r[1]) for r in cur.fetchall()}
        cur.close(); conn.close()
        return result
    except Exception as e:
        logger.warning("Sentiment: %s", e); return {}


def get_premarket_context() -> dict:
    ctx = {
        "india_vix": None, "vix_signal": "NORMAL",
        "sgx_change": None, "us_sentiment": "UNKNOWN",
        "global_signal": "NEUTRAL",
        "results_today": set(),
        "results_3days": set(),   # NEW: next 3 days results
        "announcements": [],
        "preopen_prices": {}, "breadth": {},
        "bulk_deals": set(),      # NEW: yesterday's bulk deals
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

        # Results today AND next 3 days
        cur.execute("SELECT UPPER(symbol) FROM investmitra.corporate_events WHERE event_date=CURRENT_DATE AND category='RESULTS'")
        ctx["results_today"] = {r[0] for r in cur.fetchall()}

        cur.execute("SELECT UPPER(symbol) FROM investmitra.corporate_events WHERE event_date BETWEEN CURRENT_DATE AND CURRENT_DATE+INTERVAL '3 days' AND category='RESULTS'")
        ctx["results_3days"] = {r[0] for r in cur.fetchall()}

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

    ctx["preopen_prices"] = get_nse_preopen_prices()
    ctx["breadth"]        = get_nse_market_breadth()
    ctx["bulk_deals"]     = get_nse_bulk_deals()
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

    breadth  = ctx.get("breadth", {})
    nifty_b  = breadth.get("NIFTY 50", {})
    adv, dec = nifty_b.get("advances", 0), nifty_b.get("declines", 0)
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
    if ctx["bulk_deals"]:
        print(f"  📦 Bulk deals: {len(ctx['bulk_deals'])} stocks with institutional activity")
    if ctx["results_today"]:
        print(f"\n  ⚠️  Results TODAY: {', '.join(list(ctx['results_today'])[:5])}")
    if ctx["results_3days"] - ctx["results_today"]:
        print(f"  📅 Results next 3 days: {', '.join(list(ctx['results_3days']-ctx['results_today'])[:5])}")
    d_e = "🟢" if direction=="BULLISH" else "🔴" if direction=="BEARISH" else "🟡"
    print(f"\n  {d_e} DIRECTION: {direction}")
    if ctx["vix_signal"]=="HIGH": print(f"  🚨 HIGH VIX — AVOIDING INTRADAY")
    print(f"{'='*65}\n")
    return direction, change_pct


def get_sector_quotes(kite: KiteConnect) -> dict[str, float]:
    try:
        unique = list(set(SECTOR_INDEX_MAP.values()))
        quotes = kite.quote(unique)
        result = {}
        for key, data in quotes.items():
            prev = data.get("ohlc", {}).get("close", 0)
            last = data.get("last_price", 0)
            if prev and last:
                result[key] = (last - prev) / prev * 100
        return result
    except Exception as e:
        logger.warning("Sector quotes: %s", e); return {}


def get_dynamic_gappers(kite, existing_symbols: set, ctx: dict) -> list[dict]:
    """
    Dynamic gap scanner ? runs at 9:32 AM after opens captured.
    Scans top 200 NSE stocks by traded value for genuine gaps.
    Adds any gapping stock not already in watchlist.
    """
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=15)
        cur  = conn.cursor()

        cur.execute("""
            SELECT cm.nse_symbol, cm.market_cap_category,
                   ds.investmitra_score, ds.sector,
                   ep.close AS prev_close,
                   AVG(ep.volume) OVER (PARTITION BY ep.isin) AS avg_vol,
                   AVG(ep.close * ep.volume) OVER (PARTITION BY ep.isin) AS avg_traded
            FROM investmitra.equity_prices ep
            JOIN investmitra.company_master cm ON ep.isin = cm.isin
            LEFT JOIN investmitra.daily_scores ds ON ep.isin = ds.isin
                AND ds.score_date = (SELECT MAX(score_date) FROM investmitra.daily_scores)
            WHERE ep.trade_date = (SELECT MAX(trade_date) FROM investmitra.equity_prices)
              AND cm.nse_symbol IS NOT NULL
              AND ep.close BETWEEN 50 AND 20000
              AND ep.close * ep.volume >= 2000000
            ORDER BY ep.close * ep.volume DESC
            LIMIT 200
        """)
        rows = cur.fetchall()
        cur.close(); conn.close()

        candidates = []
        for row in rows:
            sym = row[0]
            if sym in existing_symbols or not sym: continue
            candidates.append({
                'symbol':              sym,
                'market_cap_category': row[1] or 'MID',
                'investmitra_score':   float(row[2] or 50),
                'sector':              row[3] or '',
                'prev_close':          float(row[4] or 0),
                'avg_vol':             float(row[5] or 0),
                'avg_traded':          float(row[6] or 0),
                'quality_score':       float(row[2] or 50),
                'screen_count':        0,
                'piotroski_score':     0,
                'bulk_deal':           False,
                'prev_day_chg':        0,
                'company_name':        sym,
            })

        if not candidates:
            return []

        results_today = ctx.get('results_today', set())
        dynamic_gappers = []

        for i in range(0, len(candidates), 50):
            batch = candidates[i:i+50]
            batch_syms = [c['symbol'] for c in batch]
            try:
                quotes = kite.quote([f"NSE:{s}" for s in batch_syms])
            except:
                continue

            for c in batch:
                sym = c['symbol']
                if sym in results_today: continue
                q      = quotes.get(f"NSE:{sym}", {})
                ohlc   = q.get("ohlc", {})
                open_p = float(ohlc.get("open", 0))
                prev   = float(ohlc.get("close", 0)) or c['prev_close']
                ltp    = float(q.get("last_price", 0))
                vol    = int(q.get("volume", 0))
                if not open_p or not prev or not ltp: continue

                gap_pct = (open_p - prev) / prev * 100
                avg_vol = c['avg_vol'] or 100000

                from datetime import datetime, timezone, timedelta
                _ist   = timezone(timedelta(hours=5, minutes=30))
                _early = datetime.now(_ist).hour < 10
                if _early: avg_vol *= 0.4

                gap_type, _ = classify_gap(gap_pct, vol, avg_vol)
                cap    = c['market_cap_category']
                thresh = GAP_THRESHOLDS.get('momentum', 0.30)
                if cap in ('MICRO','SMALL'): thresh *= 0.7

                if (abs(gap_pct) >= thresh and
                        gap_type not in ('exhaustion','fade_risk','small_gap')):
                    c['gap_pct']  = gap_pct
                    c['gap_type'] = gap_type
                    c['ltp']      = ltp
                    dynamic_gappers.append(c)
                    logger.info("Dynamic gapper: %s gap=%.2f%% (%s)", sym, gap_pct, gap_type)

        logger.info("Dynamic scan: %d new gappers from top-200", len(dynamic_gappers))
        return dynamic_gappers[:20]

    except Exception as e:
        logger.warning("Dynamic gap scan failed: %s", e)
        return []


def get_rvol_baseline() -> dict[str, float]:
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=10)
        cur  = conn.cursor()
        cur.execute("SELECT isin, AVG(volume) FROM investmitra.equity_prices WHERE trade_date>=CURRENT_DATE-INTERVAL '30 days' AND trade_date<CURRENT_DATE GROUP BY isin HAVING AVG(volume)>0")
        result = {r[0]: float(r[1]) for r in cur.fetchall()}
        cur.close(); conn.close()
        return result
    except Exception as e:
        logger.warning("RVOL: %s", e); return {}


def get_intraday_watchlist(ctx: dict) -> tuple[list[dict], list[dict]]:
    """
    Universe: MID/LARGE/SMALL/MICRO
    Filter by TRADED VALUE (not just volume) — catches high-price stocks
    Exclude: results next 3 days, sensitive announcements,
             stocks up/down >5% yesterday
    """
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("SELECT UPPER(symbol) FROM investmitra.nse_announcements WHERE ann_datetime>=NOW()-INTERVAL '12 hours' AND is_sensitive=TRUE")
    sensitive = {r[0] for r in cur.fetchall()}

    cur.execute("""
        WITH avg_stats AS (
            SELECT isin,
                   AVG(volume) AS avg_vol,
                   AVG(close) AS avg_price,
                   AVG(volume)*AVG(close) AS avg_traded_value,
                   -- Yesterday's change %
                   (MAX(CASE WHEN trade_date=(SELECT MAX(t) FROM (SELECT DISTINCT trade_date AS t FROM investmitra.equity_prices WHERE trade_date<CURRENT_DATE ORDER BY t DESC LIMIT 1) x) THEN close END)
                    - MAX(CASE WHEN trade_date=(SELECT MAX(t) FROM (SELECT DISTINCT trade_date AS t FROM investmitra.equity_prices WHERE trade_date<CURRENT_DATE ORDER BY t DESC LIMIT 2) x LIMIT 1 OFFSET 1) THEN close END))
                   / NULLIF(MAX(CASE WHEN trade_date=(SELECT MAX(t) FROM (SELECT DISTINCT trade_date AS t FROM investmitra.equity_prices WHERE trade_date<CURRENT_DATE ORDER BY t DESC LIMIT 2) x LIMIT 1 OFFSET 1) THEN close END), 0) * 100
                   AS prev_day_chg
            FROM investmitra.equity_prices
            WHERE trade_date>=CURRENT_DATE-INTERVAL '30 days'
            GROUP BY isin
            HAVING AVG(close) BETWEEN 50 AND 20000
               AND AVG(volume)*AVG(close) >= 2000000
        )
        SELECT ds.isin, ds.company_name, cm.nse_symbol, ds.sector,
               cm.market_cap_category, ds.investmitra_score, ds.signal,
               ds.momentum_score,
               ROUND(av.avg_vol::numeric,0), ROUND(av.avg_price::numeric,2),
               COALESCE(ss.screen_count,0), COALESCE(vq.piotroski_score,0),
               COALESCE(vq.graham_criteria_met,0),
               ROUND(av.avg_traded_value::numeric,0),
               ROUND(av.prev_day_chg::numeric,2)
        FROM investmitra.daily_scores ds
        JOIN investmitra.company_master cm ON ds.isin=cm.isin
        JOIN avg_stats av ON ds.isin=av.isin
        LEFT JOIN (SELECT isin, COUNT(DISTINCT screen_name) AS screen_count
                   FROM investmitra.screener_signals
                   WHERE signal_date=(SELECT MAX(signal_date) FROM investmitra.screener_signals)
                   GROUP BY isin) ss ON ds.isin=ss.isin
        LEFT JOIN investmitra.value_quality vq ON ds.isin=vq.isin
        WHERE ds.score_date=(SELECT MAX(score_date) FROM investmitra.daily_scores)
          AND cm.nse_symbol IS NOT NULL
                    AND cm.market_cap_category IN ('MID','LARGE','SMALL','MICRO')
          -- All cap categories included
          -- F&O check only applied to SHORT signals (in _check_signal)
        ORDER BY ds.investmitra_score DESC
    """)
    rows = cur.fetchall()
    cur.close(); conn.close()

    preopen    = ctx.get("preopen_prices", {})
    bulk_deals = ctx.get("bulk_deals", set())
    long_list  = []
    short_list = []

    for r in rows:
        symbol     = r[2]
        cap        = r[4]
        avg_traded = float(r[13] or 0)
        prev_chg   = float(r[14] or 0)

        if not symbol: continue

        # Skip results next 3 days
        if symbol.upper() in ctx["results_3days"]: continue
        if symbol.upper() in sensitive: continue

        # Skip if already moved too much yesterday (chasing)
        if abs(prev_chg) > 5.0:
            logger.debug("Skip %s — prev day move %.1f%%", symbol, prev_chg)
            continue

        # Traded value filter (universal)
        if avg_traded < 5_000_000:  # Min ₹50L = ₹5M
            continue

        inv   = float(r[5] or 0)
        sc    = int(r[10] or 0)
        piots = int(r[11] or 0)
        grah  = int(r[12] or 0)

        quality = (
            (inv/100)*0.50 + min(sc/20,1.0)*0.20 +
            (piots/9)*0.15 + (grah/4)*0.15
        ) * 100

        po_price    = preopen.get(symbol, 0)
        avg_price   = float(r[9] or 0)
        preopen_gap = round((po_price - avg_price) / avg_price * 100, 2) if po_price and avg_price else 0.0
        in_bulk     = symbol.upper() in bulk_deals

        stock = {
            "isin": r[0], "company_name": r[1], "symbol": symbol,
            "sector": r[3], "cap": cap,
            "investmitra_score": inv, "signal": r[6],
            "momentum_score": float(r[7] or 0),
            "avg_volume": int(r[8] or 0), "avg_price": avg_price,
            "avg_traded": avg_traded,
            "screen_count": sc, "piotroski": piots, "graham": grah,
            "quality_score": round(quality, 2),
            "preopen_gap": preopen_gap,
            "prev_day_chg": prev_chg,
            "in_bulk_deal": in_bulk,
        }

        cap = stock.get("market_cap_category", "MID")
        # Same threshold for all caps - 55 minimum
        long_thresh  = 55
        short_thresh = 40
        if inv >= long_thresh:   long_list.append(stock)
        elif inv <= short_thresh: short_list.append(stock)
        # High quality stocks also added to short list for bearish days
        elif inv >= 50: short_list.append({**stock, "bearish_candidate": True})

    long_list  = sorted(long_list,  key=lambda x: x["quality_score"], reverse=True)[:50]
    short_list = sorted(short_list, key=lambda x: x["investmitra_score"])[:10]
    return long_list, short_list


def classify_gap(gap_pct, volume, avg_volume) -> tuple[str, float]:
    abs_gap    = abs(gap_pct)
    rvol       = volume / avg_volume if avg_volume > 0 else 1
    high_vol   = rvol > 1.5
    strong_vol = rvol > 2.5
    if abs_gap > 4.0:                              return "exhaustion", 0.4
    elif abs_gap > 2.0 and strong_vol:             return "continuation_strong", 1.3
    elif abs_gap > 2.0 and high_vol:               return "continuation", 1.1
    elif abs_gap > 2.0:                            return "exhaustion", 0.4
    elif abs_gap > 0.5 and strong_vol:             return "continuation", 1.1
    elif abs_gap > 0.3 and high_vol:               return "continuation", 1.0
    elif abs_gap > 0.5:                            return "fade_risk", 0.5
    elif abs_gap > 0.3:                            return "fade_risk", 0.3
    else:                                          return "small_gap", 0.2


class DailyRiskManager:
    def __init__(self):
        self.daily_pnl          = 0.0
        self.daily_brokerage    = 0.0
        self.trades_today       = 0
        self.consecutive_losses = 0
        self.positions          = {}

    @property
    def net_pnl(self): return self.daily_pnl - self.daily_brokerage

    def can_trade(self) -> tuple[bool, str]:
        if self.net_pnl <= -MAX_DAILY_LOSS_INR:
            return False, f"Daily loss ₹{self.net_pnl:.0f}"
        if self.trades_today >= MAX_POSITIONS * 4:
            return False, f"Max trades ({self.trades_today})"
        if len(self.positions) >= MAX_POSITIONS:
            return False, f"Max positions ({len(self.positions)})"
        if self.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            return False, f"Consecutive losses: {self.consecutive_losses}"
        return True, "OK"

    def open_position(self, symbol, entry, stop, size, target, atr):
        self.positions[symbol] = {
            "entry": entry, "stop": stop, "size": size, "target": target,
            "partial_done": False, "partial_size": size // 2,
            "atr": atr, "signal_time": datetime.now(IST),
            "trail_level": 0,  # tracks how many 1R moves made
        }
        self.trades_today    += 1
        self.daily_brokerage += BROKERAGE_PER_TRADE

    def close_position(self, symbol, exit_price):
        if symbol not in self.positions: return 0
        pos = self.positions.pop(symbol)
        pnl = (exit_price - pos["entry"]) * pos["size"]
        self.daily_pnl += pnl
        self.consecutive_losses = 0 if pnl > 0 else self.consecutive_losses + 1
        return pnl


class IntradayEngine:
    def __init__(self, long_list, short_list, token_map, prev_close,
                 market_direction, ctx, rvol_baseline, key_levels,
                 sector_quotes, sentiment):
        self.long_map         = {s["symbol"]: s for s in long_list}
        self.short_map        = {s["symbol"]: s for s in short_list}
        self.all_stocks       = {**self.long_map, **self.short_map}
        self.token_map        = token_map
        self.rev_tokens       = {v: k for k, v in token_map.items()}
        self.prev_close       = prev_close
        self.market_direction = market_direction
        self.ctx              = ctx
        self.vix_signal       = ctx["vix_signal"]
        self.rvol_baseline    = rvol_baseline
        self.key_levels       = key_levels
        self.sector_quotes    = sector_quotes
        self.sentiment        = sentiment
        self.breadth          = ctx.get("breadth", {})

        self.vwap             = defaultdict(float)
        self.cum_vol          = defaultdict(int)
        self.cum_tp_vol       = defaultdict(float)
        self.or_high          = defaultdict(float)
        self.or_low           = defaultdict(lambda: float('inf'))
        self.or_set           = defaultdict(bool)

        # TRUE GAP tracking
        self.today_open       = {}
        self.open_captured    = defaultdict(bool)

        # Gap hold tracking (5 min confirmation)
        self.gap_first_seen   = {}   # symbol -> timestamp when gap first confirmed
        self.gap_direction    = {}   # symbol -> 'LONG' or 'SHORT'

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

            # Capture today's open
            if session in ("opening","momentum") and not self.open_captured[symbol]:
                ohlc_open = tick.get("ohlc", {}).get("open", 0)
                self.today_open[symbol] = ohlc_open if ohlc_open > 0 else ltp
                self.open_captured[symbol] = True

            # VWAP
            new_vol = max(0, volume - self.cum_vol[symbol])
            if new_vol > 0:
                self.cum_vol[symbol]    = volume
                self.cum_tp_vol[symbol] += ltp * new_vol
                if volume > 0:
                    self.vwap[symbol] = self.cum_tp_vol[symbol] / volume

            # Opening range
            if session == "opening":
                self.or_high[symbol] = max(self.or_high.get(symbol, 0), ltp)
                self.or_low[symbol]  = min(self.or_low.get(symbol, float('inf')), ltp)
            elif session == "momentum" and not self.or_set[symbol]:
                self.or_set[symbol] = True

            self._check_exits(symbol, ltp, session, now)

            if session in ("momentum","choppy","afternoon"):
                self._check_signal(symbol, ltp, volume, now, session)

    def _check_exits(self, symbol, ltp, session, now):
        """Check all exit conditions: partial, trailing, dead trade, reversal."""
        if symbol not in self.risk.positions: return
        pos   = self.risk.positions[symbol]
        entry = pos["entry"]
        stop  = pos["stop"]
        atr   = pos["atr"]
        risk  = abs(entry - stop)
        is_long = symbol in self.long_map

        # Stoploss hit
        if (is_long and ltp <= stop) or (not is_long and ltp >= stop):
            pnl = self.risk.close_position(symbol, ltp)
            print(f"\n  🛑 STOPLOSS: {symbol} @ ₹{ltp:.2f} | Net: ₹{pnl:.0f}\n")
            try:
                from order_manager import notify as tg_notify
                tg_notify(f"STOPLOSS - {symbol}\nExit: {ltp:.2f}\nNet: {pnl:.0f}\nDaily P&L: {self.risk.net_pnl:.0f}")
            except: pass
            return

        # Gap reversal exit: price crosses back through open
        today_open = self.today_open.get(symbol, entry)
        if is_long and ltp < today_open * 0.998:
            pnl = self.risk.close_position(symbol, ltp)
            print(f"\n  🔄 GAP REVERSAL EXIT: {symbol} @ ₹{ltp:.2f} | Gap filled | Net: ₹{pnl:.0f}\n")
            try:
                from order_manager import notify as tg_notify
                tg_notify(f"GAP REVERSAL EXIT - {symbol}\nExit: {ltp:.2f}\nNet: {pnl:.0f}\nDaily P&L: {self.risk.net_pnl:.0f}")
            except: pass
            return

        # Dead trade exit: no movement after 45 min
        elapsed = (now - pos["signal_time"]).total_seconds() / 60
        if elapsed > DEAD_TRADE_MINUTES and not pos["partial_done"]:
            pnl = self.risk.close_position(symbol, ltp)
            print(f"\n  ⏰ DEAD TRADE EXIT: {symbol} @ ₹{ltp:.2f} | No move in {DEAD_TRADE_MINUTES}min | Net: ₹{pnl:.0f}\n")
            try:
                from order_manager import notify as tg_notify
                tg_notify(f"DEAD TRADE EXIT - {symbol}\nNo move in {DEAD_TRADE_MINUTES}min\nExit: {ltp:.2f}\nNet: {pnl:.0f}\nDaily P&L: {self.risk.net_pnl:.0f}")
            except: pass
            return

        # Partial exit at 1R
        if not pos["partial_done"]:
            if (is_long and ltp >= entry + risk) or (not is_long and ltp <= entry - risk):
                pos["partial_done"] = True
                partial_size = pos["partial_size"]
                pnl = abs(ltp - entry) * partial_size
                self.risk.daily_pnl += pnl
                pos["stop"] = entry  # Move to breakeven
                pos["size"] -= partial_size
                net = pnl - BROKERAGE_PER_TRADE // 2
                print(f"\n  💰 PARTIAL EXIT: {symbol} — {partial_size} sh @ ₹{ltp:.2f} | Net: +₹{net:.0f}")
                try:
                    from order_manager import notify as tg_notify
                    tg_notify(f"PARTIAL EXIT - {symbol}\\n{partial_size} shares @ {ltp:.2f}\\nNet: +{net:.0f}\\nStop -> breakeven\\nDaily P&L: {self.risk.net_pnl:.0f}")
                except: pass
                print(f"  📍 Stop → breakeven ₹{entry:.2f} | Remaining: {pos['size']} shares\n")

        # Trailing stop: move stop by ATR/2 after each additional 1R
        elif pos["partial_done"] and pos["size"] > 0:
            moves_made = int((ltp - entry) / risk) if is_long else int((entry - ltp) / risk)
            if moves_made > pos["trail_level"] + 1:
                pos["trail_level"] = moves_made - 1
                new_stop = round(entry + (pos["trail_level"] * risk * 0.5), 2) if is_long else \
                           round(entry - (pos["trail_level"] * risk * 0.5), 2)
                if (is_long and new_stop > pos["stop"]) or (not is_long and new_stop < pos["stop"]):
                    pos["stop"] = new_stop
                    print(f"\n  📈 TRAILING STOP: {symbol} → ₹{new_stop:.2f}\n")

        # Session close
        if session == "closing":
            pnl = self.risk.close_position(symbol, ltp)
            if pnl is not None:
                print(f"\n  🏁 SESSION EXIT: {symbol} @ ₹{ltp:.2f} | Net: ₹{pnl:.0f}\n")
                try:
                    from order_manager import notify as tg_notify
                    tg_notify(f"3PM EXIT - {symbol}\nExit: {ltp:.2f}\nNet: {pnl:.0f}\nDaily P&L: {self.risk.net_pnl:.0f}")
                except: pass

    def _compute_opportunity_score(self, symbol, ltp, volume, true_gap_pct, session) -> tuple[float, dict]:
        stock      = self.all_stocks.get(symbol, {})
        vwap       = self.vwap.get(symbol, ltp)
        or_h       = self.or_high.get(symbol, ltp)
        or_l       = self.or_low.get(symbol, float('inf'))
        isin       = stock.get("isin", "")
        sector     = stock.get("sector", "")
        kl         = self.key_levels.get(symbol, {})

        # RVOL
        avg_daily  = self.rvol_baseline.get(isin, stock.get("avg_volume", 1))
        now        = datetime.now(IST)
        mkt_min    = now.hour*60+now.minute-(9*60+15)
        frac       = max(mkt_min/375, 0.05)
        rvol       = volume / (avg_daily * frac) if avg_daily * frac > 0 else 1

        # Gap classification
        avg_vol    = stock.get("avg_volume", 1)
        # Early morning RVOL adjustment - volume builds up after 10 AM
        _now_ist = datetime.now(IST)
        _early_morning = _now_ist.hour < 10  # Before 10 AM
        
        # Adjust avg_vol for early morning (volume is naturally lower)
        if _early_morning:
            avg_vol = avg_vol * 0.4  # Expect only 40% of daily avg before 10 AM
        
        gap_type, gap_mult = classify_gap(true_gap_pct, volume, avg_vol)

        # Sector RS
        sector_key = SECTOR_INDEX_MAP.get(sector, "NSE:NIFTY 50")
        sector_chg = self.sector_quotes.get(sector_key, 0)
        nifty_data = self.breadth.get("NIFTY 50", {})
        nifty_chg  = nifty_data.get("pct_change", 0)
        stock_chg  = true_gap_pct
        if stock_chg > sector_chg > nifty_chg and true_gap_pct > 0: sector_rs = 90
        elif stock_chg > sector_chg:                                  sector_rs = 70
        elif stock_chg > nifty_chg:                                   sector_rs = 55
        else:                                                          sector_rs = 35

        # Key levels
        ma20 = kl.get("ma20", 0); ma50 = kl.get("ma50", 0)
        high_52w = kl.get("high_52w", 0); low_52w = kl.get("low_52w", 0)
        kl_score = 50
        if ma20 and ma50:
            if ltp > ma20 and ltp > ma50: kl_score = 70
            elif ltp > ma20:              kl_score = 55
            else:                         kl_score = 30
        # 52-week high breakout bonus
        if high_52w and ltp >= high_52w * 0.99:
            kl_score += 20  # Near 52-week high = breakout potential
        # 52-week low bounce bonus for shorts
        if low_52w and ltp <= low_52w * 1.01:
            kl_score += 15

        # Breadth
        adv = nifty_data.get("advances", 0); dec = nifty_data.get("declines", 0)
        ad_ratio = adv / dec if dec > 0 else 1.0
        breadth_score = min(ad_ratio/3.0*100, 100) if true_gap_pct>0 and ad_ratio>1 else 30

        # ORB
        orb_score = 0
        if self.or_set.get(symbol):
            or_range = or_h - or_l
            if or_range > 0 and or_l < float('inf'):
                if ltp > or_h:   orb_score = min((ltp-or_h)/or_range*100, 100)
                elif ltp < or_l: orb_score = min((or_l-ltp)/or_range*100, 100)

        # Sentiment
        sent = self.sentiment.get(symbol, 0)
        sent_score = 80 if sent>0.3 else 20 if sent<-0.3 else 50

        # Bulk deal bonus
        bulk_score = 70 if stock.get("in_bulk_deal") else 50

        # Price holding above open
        today_open    = self.today_open.get(symbol, ltp)
        holding_score = 80 if (true_gap_pct>0 and ltp>=today_open) or \
                              (true_gap_pct<0 and ltp<=today_open) else 25

        # Pre-open conviction
        preopen_gap   = stock.get("preopen_gap", 0)
        preopen_score = 80 if abs(preopen_gap)>0.5 else 60 if abs(preopen_gap)>0.2 else 40

        vwap_score  = 80 if ltp > vwap else 20
        rvol_score  = min((rvol-1)/3.0*100, 100) if rvol > 1 else 0
        gap_score   = min(abs(true_gap_pct)/2.0, 1.0) * 100 * gap_mult
        regime_score= 70 if self.market_direction=="BULLISH" else 30 if self.market_direction=="BEARISH" else 50
        sess_mult   = {"momentum":1.0,"choppy":0.7,"afternoon":0.85}.get(session, 0.5)

        # Load Opus weights
        try:
            import json as _j
            _conn = psycopg2.connect(NEON_URL, connect_timeout=5)
            _cur  = _conn.cursor()
            _cur.execute("SELECT weights FROM investmitra.signal_weights WHERE effective_date<=CURRENT_DATE ORDER BY effective_date DESC LIMIT 1")
            _row  = _cur.fetchone()
            _cur.close(); _conn.close()
            _w = _row[0] if isinstance(_row[0], dict) else _j.loads(_row[0]) if _row else {}
        except:
            _w = {}

        opp = (
            gap_score     * _w.get("gap_score",     0.15) +
            rvol_score    * _w.get("rvol_score",    0.12) +
            vwap_score    * _w.get("vwap_score",    0.10) +
            orb_score     * _w.get("orb_score",     0.12) +
            holding_score * _w.get("holding_score", 0.10) +
            sector_rs     * _w.get("sector_rs",     0.12) +
            breadth_score * _w.get("breadth_score", 0.05) +
            regime_score  * _w.get("regime_score",  0.05) +
            kl_score      * _w.get("kl_score",      0.08) +
            sent_score    * _w.get("sent_score",     0.04) +
            bulk_score    * _w.get("bulk_score",     0.04) +
            preopen_score * _w.get("preopen_score",  0.03)
        ) * sess_mult

        details = {
            "gap_type": gap_type, "gap_score": round(gap_score,1),
            "rvol": round(rvol,2), "rvol_score": round(rvol_score,1),
            "vwap_score": vwap_score, "orb_score": round(orb_score,1),
            "holding_score": holding_score, "preopen_score": preopen_score,
            "sector_rs": round(sector_rs,1), "breadth": round(breadth_score,1),
            "kl_score": kl_score, "sector_chg": round(sector_chg,2),
            "stock_vs_sector": round(stock_chg-sector_chg,2),
            "sentiment": round(sent,2), "bulk_deal": stock.get("in_bulk_deal",False),
            "today_open": round(today_open,2), "52w_high": round(high_52w,2),
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
        kl    = self.key_levels.get(symbol, {})
        if not prev: return

        # Need today's open captured
        today_open = self.today_open.get(symbol, 0)
        if today_open == 0:
            # Fallback: fetch open from Kite quote
            try:
                q = self.kite.quote([f"NSE:{symbol}"])
                today_open = float(q.get(f"NSE:{symbol}", {}).get("ohlc", {}).get("open", 0))
                if today_open > 0:
                    self.today_open[symbol] = today_open
                else:
                    return
            except:
                return

        # TRUE GAP
        true_gap_pct = (today_open - prev) / prev * 100

        # GAP HOLD CONFIRMATION (5 minutes)
        gap_thresh = GAP_THRESHOLDS.get(session, 0.4)
        # Lower threshold for MICRO/SMALL ? they gap more
        cap = stock.get("market_cap_category", "MID")
        if cap in ("MICRO", "SMALL"):
            gap_thresh *= 0.7  # 30% lower for small caps
        if self.vix_signal == "ELEVATED": gap_thresh *= 1.5

        if abs(true_gap_pct) > gap_thresh:
            # First time we see this gap — record timestamp
            if symbol not in self.gap_first_seen:
                self.gap_first_seen[symbol] = now
                self.gap_direction[symbol] = "LONG" if true_gap_pct > 0 else "SHORT"

            # Check if gap has held for 5 minutes
            elapsed_mins = (now - self.gap_first_seen[symbol]).total_seconds() / 60
            if elapsed_mins < GAP_HOLD_MINUTES:
                return  # Wait for gap to confirm

            # Check gap direction hasn't flipped
            current_dir = "LONG" if true_gap_pct > 0 else "SHORT"
            if current_dir != self.gap_direction.get(symbol):
                del self.gap_first_seen[symbol]
                return
        else:
            # Gap disappeared — reset
            if symbol in self.gap_first_seen:
                del self.gap_first_seen[symbol]
            return

        above_vwap = ltp > vwap * 1.001
        below_vwap = ltp < vwap * 0.999

        quality = stock.get("quality_score", 50)
        opp, details = self._compute_opportunity_score(symbol, ltp, volume, true_gap_pct, session)

        # Exhaustion gaps: always skip
        if details["gap_type"] == "exhaustion":
            return

        # fade_risk: only allow if RVOL > 2.5x AND quality > 65
        # Otherwise skip — Sonnet confirmed fade_risk consistently loses
        if details["gap_type"] == "fade_risk":
            if details["rvol"] < 2.5 or quality < 65:
                logger.debug("Skip %s — fade_risk with weak RVOL %.1fx", symbol, details["rvol"])
                return

        final = quality * 0.40 + opp * 0.60
        if final < 48: return

        # Check market breadth for bearish bias
        breadth     = getattr(self, "ctx", {}).get("breadth", {})
        ad_ratio    = breadth.get("adv_ratio", 1.0) if isinstance(breadth, dict) else 1.0
        weak_market = ad_ratio < 0.3 or self.market_direction == "BEARISH"

        direction = None

        # LONG: quality stock gapping up in neutral/bullish market
        if (symbol in self.long_map and
                self.market_direction in ("BULLISH","NEUTRAL") and
                true_gap_pct > gap_thresh and
                ltp >= today_open * 0.998 and
                above_vwap and
                score >= (55 if stock.get("market_cap_category","MID") in ("MICRO","SMALL") else 60)):
            direction = "LONG"

        # SHORT Option 1: dedicated short stock (low quality) gapping down
        elif (symbol in self.short_map and
                self.market_direction in ("BEARISH","NEUTRAL") and
                true_gap_pct < -gap_thresh and
                ltp <= today_open * 1.002 and
                below_vwap and score <= 40):
            direction = "SHORT"

        # SHORT Option 2: HIGH QUALITY stock gapping DOWN on weak/bearish day
        # Only F&O eligible stocks can be shorted intraday reliably
        elif (symbol in self.long_map and
                weak_market and
                true_gap_pct < -gap_thresh and
                ltp <= today_open * 1.002 and
                below_vwap and score >= 55 and
                self._is_fo_eligible(symbol) and
                abs(true_gap_pct) > 0.5):  # Require stronger gap for quality shorts
            direction = "SHORT"
            logger.info("Bearish SHORT: %s gap %.2f%% breadth %.1fx (F&O eligible)", symbol, true_gap_pct, ad_ratio)

        if not direction: return

        # ATR from 14-day daily range
        atr = kl.get("atr14", 0) or kl.get("daily_range", 0) or ltp * 0.01

        stop   = round(ltp - atr*ATR_STOP_MULT, 2) if direction=="LONG" else round(ltp + atr*ATR_STOP_MULT, 2)
        target = round(ltp + atr*ATR_TARGET_MULT, 2) if direction=="LONG" else round(ltp - atr*ATR_TARGET_MULT, 2)
        stop_dist = abs(ltp - stop)
        if stop_dist == 0: return

        size = max(1, min(int(MAX_RISK_PER_TRADE_INR/stop_dist), int(MAX_CAPITAL_PER_TRADE/ltp)))

        expected_net = (abs(target - ltp) * size * 0.5) - BROKERAGE_PER_TRADE
        if expected_net < MIN_NET_PROFIT: return

        self.risk.open_position(symbol, ltp, stop, size, target, atr)

        self.signals[symbol] = dict(
            symbol=symbol, direction=direction, entry=ltp,
            target=target, stoploss=stop, atr=round(atr,2),
            true_gap=round(true_gap_pct,2), today_open=today_open,
            vwap=vwap, final_score=final,
            quality_score=quality, opp_score=opp,
            position_size=size, stop_dist=round(stop_dist,2),
            risk_inr=round(stop_dist*size,0),
            expected_net=round(expected_net,0),
            session=session, time=now.strftime("%H:%M:%S"),
            details=details, cap=stock.get("cap","?"),
            screens=stock.get("screen_count",0),
            piotroski=stock.get("piotroski",0),
            in_bulk=stock.get("in_bulk_deal",False),
        )
        self._print_signal(self.signals[symbol], stock)

        # Send Telegram alert immediately
        try:
            from order_manager import notify as tg_notify
            sig = self.signals[symbol]
            d   = sig['details']
            direction = sig['direction']
            emoji = 'LONG' if direction == 'LONG' else 'SHORT'
            tg_notify(
                f"{emoji} SIGNAL - {symbol} [{sig['cap']}]\n"
                f"{stock.get('company_name','')[:30]}\n\n"
                f"Entry:   {sig['entry']:,.2f}\n"
                f"Target:  {sig['target']:,.2f} ({abs(sig['entry']-sig['target'])/sig['entry']*100:.1f}%)\n"
                f"Stop:    {sig['stoploss']:,.2f}\n"
                f"Size:    {sig['position_size']} shares\n"
                f"Risk:    {sig['risk_inr']:.0f} + 80 brokerage\n"
                f"Gap:     {sig['true_gap']:+.2f}% ({d['gap_type']})\n"
                f"RVOL:    {d['rvol']:.1f}x\n"
                f"Score:   {sig['final_score']:.1f}\n"
                f"ATR:     {sig['atr']:.2f}\n\n"
                f"Open Kite app and place order!"
            )
        except Exception as e:
            pass

    def _print_signal(self, sig, stock):
        emoji  = "🟢 LONG " if sig["direction"]=="LONG" else "🔴 SHORT"
        pct    = abs(sig["entry"]-sig["target"])/sig["entry"]*100
        sl_pct = abs(sig["entry"]-sig["stoploss"])/sig["entry"]*100
        d      = sig["details"]
        bulk   = " 📦 BULK DEAL" if sig["in_bulk"] else ""
        cap52  = f" 🏆 52W HIGH" if d.get("52w_high") and sig["entry"] >= d["52w_high"]*0.99 else ""
        print(f"\n{'='*65}")
        print(f"  {emoji} — {sig['symbol']} [{sig['cap']}]{bulk}{cap52}")
        print(f"  {stock.get('company_name','')[:50]}")
        print(f"{'='*65}")
        print(f"  Entry:        ₹{sig['entry']:,.2f}")
        print(f"  Target:       ₹{sig['target']:,.2f}  (+{pct:.1f}%)")
        print(f"  Stoploss:     ₹{sig['stoploss']:,.2f}  (-{sl_pct:.1f}%) [ATR: ₹{sig['atr']:.2f}]")
        print(f"  Size:         {sig['position_size']} sh × ₹{sig['entry']:.0f} = ₹{sig['position_size']*sig['entry']:,.0f}")
        print(f"  Risk:         ₹{sig['risk_inr']:.0f} + ₹{BROKERAGE_PER_TRADE} brokerage")
        print(f"  Expected net: ₹{sig['expected_net']:.0f} (at 50% exit)")
        print(f"  Exits:        50% at 1R → trail → reversal/dead/3PM")
        print(f"  True Gap:     {sig['true_gap']:+.2f}% (open ₹{sig['today_open']:.2f} vs prev ₹{self.prev_close.get(sig['symbol'],0):.2f})")
        print(f"  Gap held:     {GAP_HOLD_MINUTES} min confirmed")
        print(f"  VWAP:         ₹{sig['vwap']:,.2f}")
        print(f"  RVOL:         {d['rvol']:.1f}x | ORB: {d['orb_score']:.0f} | Hold: {d['holding_score']:.0f}")
        print(f"  Sector RS:    {d['stock_vs_sector']:+.2f}% vs sector | Sector: {d['sector_chg']:+.2f}%")
        print(f"  Sentiment:    {d['sentiment']:+.2f} | Bulk: {'YES' if d['bulk_deal'] else 'no'}")
        print(f"  Score:        {sig['final_score']:.1f} (Q:{sig['quality_score']:.0f} | O:{sig['opp_score']:.0f})")
        print(f"  F-Score: {sig['piotroski']} | Screens: {sig['screens']} | {sig['session']}")
        print(f"  Time:         {sig['time']} | Net P&L: ₹{self.risk.net_pnl:.0f}")
        print(f"{'='*65}\n")

    def _is_fo_eligible(self, symbol: str) -> bool:
        """Check if stock is F&O eligible (can be shorted intraday)."""
        try:
            conn = psycopg2.connect(NEON_URL, connect_timeout=5)
            cur  = conn.cursor()
            cur.execute("SELECT 1 FROM investmitra.fo_stocks WHERE symbol=%s", (symbol,))
            result = cur.fetchone() is not None
            cur.close(); conn.close()
            return result
        except:
            return True  # Default allow if DB check fails

    def _save_trades_to_neon(self):
        """Auto-save all signals and exits to Neon trade_log."""
        if not self.signals:
            return
        try:
            conn = psycopg2.connect(NEON_URL, connect_timeout=10)
            conn.autocommit = True
            cur  = conn.cursor()

            # Ensure table exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS investmitra.trade_log (
                    id               SERIAL PRIMARY KEY,
                    trade_date       DATE NOT NULL,
                    symbol           VARCHAR(20),
                    direction        VARCHAR(10),
                    entry_price      DECIMAL(12,2),
                    exit_price       DECIMAL(12,2),
                    quantity         INTEGER,
                    gross_pnl        DECIMAL(12,2),
                    net_pnl          DECIMAL(12,2),
                    outcome          VARCHAR(20),
                    hold_minutes     INTEGER,
                    true_gap_pct     DECIMAL(8,4),
                    gap_type         VARCHAR(30),
                    rvol             DECIMAL(8,2),
                    sector_rs        DECIMAL(8,2),
                    final_score      DECIMAL(8,2),
                    market_direction VARCHAR(20),
                    vix_level        DECIMAL(8,2),
                    session          VARCHAR(20),
                    atr              DECIMAL(10,2),
                    capital_deployed DECIMAL(12,2),
                    created_at       TIMESTAMPTZ DEFAULT NOW()
                )
            """)

            saved = 0
            for symbol, sig in self.signals.items():
                # Get exit info from risk manager history
                entry  = sig.get("entry", 0)
                exit_p = sig.get("exit_price", entry)  # fallback to entry if no exit
                qty    = sig.get("position_size", 0)
                outcome= sig.get("outcome", "TIME_EXIT")
                d      = sig.get("details", {})

                gross = (exit_p - entry) * qty if sig["direction"]=="LONG" else (entry - exit_p) * qty
                net   = gross - 80

                cur.execute("""
                    INSERT INTO investmitra.trade_log
                        (trade_date, symbol, direction, entry_price, exit_price,
                         quantity, gross_pnl, net_pnl, outcome, hold_minutes,
                         true_gap_pct, gap_type, rvol, sector_rs,
                         final_score, market_direction, vix_level, session, atr,
                         capital_deployed)
                    VALUES (CURRENT_DATE,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    symbol, sig["direction"], entry, exit_p, qty,
                    round(gross,2), round(net,2), outcome, 45,
                    sig.get("true_gap",0), d.get("gap_type",""),
                    d.get("rvol",0), d.get("sector_rs",0),
                    sig.get("final_score",0), self.market_direction,
                    0, sig.get("session","momentum"),
                    sig.get("atr",0), round(entry*qty,2)
                ))
                saved += 1

            # Update intraday_pnl
            wins = sum(1 for s in self.signals.values() 
                      if (s.get("exit_price",s["entry"])-s["entry"])*
                         (1 if s["direction"]=="LONG" else -1) > 0)
            cur.execute("""
                INSERT INTO investmitra.intraday_pnl
                    (trade_date, trades, capital_deployed, gross_pnl, brokerage,
                     net_pnl, win_trades, loss_trades, market_direction, vix_level)
                VALUES (CURRENT_DATE,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (trade_date) DO UPDATE SET
                    trades=EXCLUDED.trades,
                    gross_pnl=EXCLUDED.gross_pnl,
                    brokerage=EXCLUDED.brokerage,
                    net_pnl=EXCLUDED.net_pnl,
                    win_trades=EXCLUDED.win_trades,
                    loss_trades=EXCLUDED.loss_trades,
                    saved_at=NOW()
            """, (
                len(self.signals),
                sum(s.get("position_size",0)*s.get("entry",0) for s in self.signals.values()),
                round(self.risk.daily_pnl,2),
                round(self.risk.daily_brokerage,2),
                round(self.risk.net_pnl,2),
                wins, len(self.signals)-wins,
                self.market_direction, 0
            ))

            cur.close(); conn.close()
            logger.info("Auto-saved %d trades to Neon", saved)
            print(f"\n  💾 {saved} trades auto-saved to Neon")

        except Exception as e:
            logger.warning("Auto-save trades failed: %s", e)

    def print_summary(self):
        longs  = [s for s in self.signals.values() if s["direction"]=="LONG"]
        shorts = [s for s in self.signals.values() if s["direction"]=="SHORT"]
        print(f"\n{'='*65}")
        print(f"  INTRADAY SUMMARY — {date.today()}")
        print(f"  Market: {self.market_direction} | VIX: {self.vix_signal}")
        print(f"  Gross: ₹{self.risk.daily_pnl:.0f} | Brokerage: ₹{self.risk.daily_brokerage:.0f} | NET: ₹{self.risk.net_pnl:.0f}")
        print(f"{'='*65}")
        # Auto-save all trades to Neon
        self._save_trades_to_neon()
        for label, lst in [("🟢 LONG", longs), ("🔴 SHORT", shorts)]:
            if lst:
                print(f"\n  {label} ({len(lst)}):")
                for s in lst:
                    cap = s['position_size']*s['entry']
                    print(f"    {s['symbol']:<12}[{s['cap']}] ₹{s['entry']:>8,.2f}→₹{s['target']:>8,.2f} SL:₹{s['stoploss']:,.2f} {s['position_size']}sh ₹{cap:,.0f} Gap:{s['true_gap']:+.1f}%")
        if not longs and not shorts:
            print("  No signals today.")
        print(f"\n  ⚠️  SQUARE OFF BY 3:00 PM")
        print(f"{'='*65}\n")



# ── Daily P&L Save ─────────────────────────────────────────────────────────────

def ensure_pnl_table(conn):
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS investmitra.intraday_pnl (
            id               SERIAL PRIMARY KEY,
            trade_date       DATE NOT NULL UNIQUE,
            trades           INTEGER DEFAULT 0,
            capital_deployed DECIMAL(12,2) DEFAULT 0,
            gross_pnl        DECIMAL(12,2) DEFAULT 0,
            brokerage        DECIMAL(12,2) DEFAULT 0,
            net_pnl          DECIMAL(12,2) DEFAULT 0,
            win_trades       INTEGER DEFAULT 0,
            loss_trades      INTEGER DEFAULT 0,
            market_direction VARCHAR(20),
            vix_level        DECIMAL(8,2),
            signals          JSONB,
            saved_at         TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.close()


def save_daily_pnl(risk_manager, signals: dict, market_direction: str, vix: float):
    """Save end-of-day P&L summary to Neon for Grafana."""
    import json
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=15)
        ensure_pnl_table(conn)
        conn.autocommit = True
        cur  = conn.cursor()

        today = datetime.now(IST).date()

        # Signals summary
        sig_summary = [{
            "symbol":    sym,
            "direction": s.get("direction"),
            "entry":     s.get("entry"),
            "target":    s.get("target"),
            "stoploss":  s.get("stoploss"),
            "gap":       round(s.get("true_gap", s.get("gap_pct", 0)), 2),
            "cap":       s.get("cap"),
            "score":     round(s.get("final_score", 0), 1),
            "atr":       s.get("atr"),
            "size":      s.get("position_size"),
        } for sym, s in signals.items()]

        # Capital deployed
        capital = sum(
            s.get("position_size", 0) * s.get("entry", 0)
            for s in signals.values()
        )

        # Win/loss
        total  = risk_manager.trades_today
        losses = risk_manager.consecutive_losses
        wins   = max(total - losses, 0)

        cur.execute("""
            INSERT INTO investmitra.intraday_pnl
                (trade_date, trades, capital_deployed, gross_pnl, brokerage,
                 net_pnl, win_trades, loss_trades, market_direction, vix_level, signals)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (trade_date) DO UPDATE SET
                trades=EXCLUDED.trades,
                capital_deployed=EXCLUDED.capital_deployed,
                gross_pnl=EXCLUDED.gross_pnl,
                brokerage=EXCLUDED.brokerage,
                net_pnl=EXCLUDED.net_pnl,
                win_trades=EXCLUDED.win_trades,
                loss_trades=EXCLUDED.loss_trades,
                signals=EXCLUDED.signals,
                saved_at=NOW()
        """, (
            today, total, round(capital,2),
            round(risk_manager.daily_pnl, 2),
            round(risk_manager.daily_brokerage, 2),
            round(risk_manager.net_pnl, 2),
            wins, losses,
            market_direction, vix,
            json.dumps(sig_summary)
        ))
        cur.close(); conn.close()
        print(f"\n  💾 Daily P&L saved → Neon (date: {today})")
        print(f"  Trades: {total} | Net: ₹{risk_manager.net_pnl:.0f} | Capital: ₹{capital:,.0f}")
    except Exception as e:
        logger.warning("P&L save failed: %s", e)


def preflight_check() -> bool:
    """Run pre-flight checks before market opens."""
    print(f"\n{'='*65}")
    print(f"  investMITRA PRE-FLIGHT CHECK — {date.today()}")
    print(f"{'='*65}")
    all_ok = True

    # 1. Kite token
    if API_KEY and ACCESS_TOKEN:
        print(f"  ✅ Kite token present")
    else:
        print(f"  ❌ Kite token MISSING — run: python scripts/kite_login.py")
        all_ok = False

    # 1b. Auto-fetch market data if today's data missing
    try:
        import subprocess
        _conn2 = psycopg2.connect(NEON_URL, connect_timeout=5)
        _cur2  = _conn2.cursor()
        _cur2.execute("SELECT COUNT(*) FROM investmitra.market_indices WHERE fetch_date=CURRENT_DATE")
        count = _cur2.fetchone()[0]
        _cur2.close(); _conn2.close()
        if count == 0:
            print("  Fetching today's market indices...")
            subprocess.run(["python", "scripts/fetch_market_indices.py"], timeout=60, cwd=os.getcwd())
            subprocess.run(["python", "scripts/fetch_global_sentiment.py"], timeout=60, cwd=os.getcwd())
            print("  Market data fetched")
        else:
            print(f"  Market indices: {count} records today")
    except Exception as e:
        print(f"  Auto-fetch failed: {e}")
        try:
            subprocess.run(["python", "scripts/fetch_market_indices.py"], timeout=60)
            subprocess.run(["python", "scripts/fetch_global_sentiment.py"], timeout=60)
        except: pass

    # 2. Neon connection + data freshness
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=5)
        cur  = conn.cursor()
        cur.execute("SELECT MAX(score_date) FROM investmitra.daily_scores")
        score_date = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM investmitra.equity_prices WHERE trade_date >= CURRENT_DATE - INTERVAL '30 days'")
        price_rows = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM investmitra.market_indices WHERE fetch_date = CURRENT_DATE")
        indices_today = cur.fetchone()[0]
        conn.close()
        fresh = score_date and (date.today() - score_date).days <= 3
        print(f"  {'✅' if fresh else '⚠️ '} Scores: latest {score_date} {'(fresh)' if fresh else '(stale — check pipeline)'}")
        print(f"  {'✅' if price_rows > 10000 else '⚠️ '} Price data: {price_rows:,} rows")
        print(f"  {'✅' if indices_today > 0 else '⚠️ '} Market indices: {'fetched today' if indices_today > 0 else 'NOT fetched today'}")
    except Exception as e:
        print(f"  ❌ Neon connection failed: {e}")
        all_ok = False

    # 3. kiteconnect library
    try:
        from kiteconnect import KiteConnect, KiteTicker
        print(f"  ✅ kiteconnect installed")
    except ImportError:
        print(f"  ❌ kiteconnect missing — pip install kiteconnect")
        all_ok = False

    # 4. Required env vars
    missing = [v for v in ['KITE_API_KEY','CC_POSTGRES_URL'] if not os.getenv(v)]
    if missing:
        print(f"  ❌ Missing env vars: {missing}")
        all_ok = False
    else:
        print(f"  ✅ Environment variables OK")

    print(f"{'='*65}")
    if all_ok:
        print(f"  🟢 ALL CHECKS PASSED — Ready to trade")
    else:
        print(f"  🔴 CHECKS FAILED — Fix issues above before trading")
    print(f"{'='*65}")

    return all_ok


def main():
    if not API_KEY or not ACCESS_TOKEN:
        print("❌ Run: python scripts/kite_login.py first"); sys.exit(1)

    # Pre-flight check
    if not preflight_check():
        sys.exit(1)

    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(ACCESS_TOKEN)

    ctx = get_premarket_context()
    market_direction, nifty_change = get_market_direction(kite, ctx)

    # Load Opus-updated weights from Neon
    global GAP_THRESHOLDS
    weights = load_signal_weights()
    if weights:
        GAP_THRESHOLDS["momentum"]  = weights.get("gap_threshold_momentum", 0.3)
        GAP_THRESHOLDS["choppy"]    = weights.get("gap_threshold_choppy", 0.6)
        GAP_THRESHOLDS["afternoon"] = weights.get("gap_threshold_afternoon", 0.4)
        logger.info("Weights loaded: gap_momentum=%.2f sector_rs=%.2f rvol=%.2f",
                    GAP_THRESHOLDS["momentum"],
                    weights.get("sector_rs", 0.12),
                    weights.get("rvol_score", 0.13))
        # Apply skip flags
        if weights.get("skip_choppy_session"):
            SESSIONS.pop("choppy", None)
            logger.info("Choppy session DISABLED by Opus")
        if weights.get("skip_fade_risk"):
            logger.info("fade_risk gaps DISABLED by Opus")

    # Morning brief to Telegram
    try:
        from order_manager import notify as tg_notify
        vix  = ctx.get('india_vix', 0) or 0
        sgx  = ctx.get('sgx_change', 0) or 0
        skip = ', '.join(list(ctx['results_today'])[:3]) or 'None'
        nxt  = ', '.join(list(ctx.get('results_3days', set()) - ctx['results_today'])[:3]) or 'None'
        ve = '??' if vix<12 else '??' if vix<16 else '??'
        de = '??' if market_direction=='BULLISH' else '??' if market_direction=='BEARISH' else '??'
        tg_notify(
            f"investMITRA MORNING BRIEF - {date.today()}\n\n"
            f"VIX: {vix:.2f} ({ctx['vix_signal']})\n"
            f"{'UP' if sgx>0 else 'DN'} SGX: {sgx:+.2f}%\n"
            f"{'UP' if ctx['us_sentiment']=='POSITIVE' else 'DN' if ctx['us_sentiment']=='NEGATIVE' else '--'} US: {ctx['us_sentiment']}\n"
            f"{'UP' if nifty_change>0.3 else 'DN' if nifty_change<-0.3 else '--'} Nifty Fut: {nifty_change:+.2f}%\n"
            f"Direction: {market_direction}\n\n"
            f"Results today: {skip}\n"
            f"Next 3 days: {nxt}\n\n"
            f"Signals from 9:35 AM"
        )
    except Exception as e:
        pass

    if ctx["vix_signal"] == "HIGH":
        print("🚨 VIX > 20 — No intraday today."); sys.exit(0)

    rvol_baseline         = get_rvol_baseline()
    long_list, short_list = get_intraday_watchlist(ctx)

    # On weak breadth days ? allow quality stocks to go SHORT too
    breadth = ctx.get("breadth", {})
    adv_ratio = breadth.get("adv_ratio", 1.0) if isinstance(breadth, dict) else 1.0
    weak_market = adv_ratio < 0.3 or market_direction == "BEARISH"

    if market_direction == "BULLISH":
        short_list = []
    elif market_direction == "BEARISH":
        long_list = []
    elif weak_market:
        # NEUTRAL but weak breadth ? keep both but flag bearish bias
        logger.info("Weak breadth (%.1fx) ? SHORT bias enabled for quality stocks", adv_ratio)

    all_stocks = long_list + short_list
    if not all_stocks:
        logger.error("No stocks in watchlist"); sys.exit(1)

    symbols       = list(set(s["symbol"] for s in all_stocks))
    token_map     = {i["tradingsymbol"]: i["instrument_token"]
                     for i in kite.instruments("NSE")
                     if i.get("tradingsymbol") in symbols and i.get("segment")=="NSE"}
    prev_close    = {s.replace("NSE:",""): d["ohlc"]["close"]
                     for s,d in kite.quote([f"NSE:{s}" for s in token_map]).items()}
    key_levels    = get_key_levels(symbols)
    sector_quotes = get_sector_quotes(kite)
    sentiment     = get_stock_sentiment(symbols)

    logger.info("Universe: %d stocks | key_levels:%d sector:%d sentiment:%d",
                len(all_stocks), len(key_levels), len(sector_quotes), len(sentiment))

    # Print watchlist
    print(f"\n{'='*65}")
    print(f"  INTRADAY WATCHLIST v10 — {date.today()} | {market_direction}")
    print(f"  TRUE GAP | 5-min hold | ATR 14-day | Traded value filter")
    print(f"  Cap: ₹{MAX_CAPITAL_PER_TRADE:,}/trade | Risk: ₹{MAX_RISK_PER_TRADE_INR}")
    print(f"{'='*65}")
    if long_list:
        print(f"\n  🟢 LONG ({len(long_list)}) — sorted by Quality:")
        print(f"  {'Symbol':<13} {'Cap':<7} {'Qual':>5} {'Score':>6} {'Scr':>4} {'F':>3} {'ATR':>7} {'Prev%':>6} {'Bulk'}")
        print(f"  {'─'*65}")
        for s in long_list:
            if s["symbol"] in token_map:
                kl  = key_levels.get(s["symbol"], {})
                atr = kl.get("atr14", 0)
                blk = "📦" if s.get("in_bulk_deal") else ""
                print(f"  {s['symbol']:<13} {s['cap']:<7} {s['quality_score']:>5.1f} {s['investmitra_score']:>6.1f} {s['screen_count']:>4} {s['piotroski']:>3} {atr:>7.1f} {s['prev_day_chg']:>+5.1f}% {blk}")
    if short_list:
        print(f"\n  🔴 SHORT ({len(short_list)}):")
        for s in short_list:
            if s["symbol"] in token_map:
                kl  = key_levels.get(s["symbol"], {})
                atr = kl.get("atr14", 0)
                print(f"  {s['symbol']:<13} {s['cap']:<7} {s['quality_score']:>5.1f} {s['investmitra_score']:>6.1f} ATR:{atr:.1f}")
    print(f"\n  Capturing opens 9:15-9:30 → gap hold 5min → signals 9:35+")
    print(f"  Exits: partial@1R | trail | reversal | dead@45min | 3PM")
    print(f"{'='*65}\n")

    tokens = list(token_map.values())
    engine = IntradayEngine(long_list, short_list, token_map, prev_close,
                            market_direction, ctx, rvol_baseline,
                            key_levels, sector_quotes, sentiment)
    engine.kite = kite  # Store kite reference for LTP fallback

    # Dynamic gap scan ? runs once after WebSocket stable
    import threading
    _dynamic_done = [False]
    def _dynamic_scan():
        import time as _time
        # Wait 60 seconds for WebSocket to fully stabilize
        _time.sleep(60)
        # Then wait until after 9:30 AM
        while True:
            now = datetime.now(IST)
            if now.hour > 9 or (now.hour == 9 and now.minute >= 30):
                break
            _time.sleep(10)
        if _dynamic_done[0]: return
        _dynamic_done[0] = True

        existing = set(engine.all_stocks.keys())
        new_gappers = get_dynamic_gappers(kite, existing, ctx)

        if new_gappers:
            # Add to engine watchlist
            for g in new_gappers:
                engine.long_map[g['symbol']] = g
                engine.all_stocks[g['symbol']] = g

            # Subscribe new tokens
            new_tokens = []
            for g in new_gappers:
                try:
                    instr = kite.ltp([f"NSE:{g['symbol']}"])
                    for k,v in instr.items():
                        tok = v.get('instrument_token')
                        if tok:
                            engine.token_map[g['symbol']] = tok
                            engine.rev_tokens[tok] = g['symbol']
                            new_tokens.append(tok)
                except: pass

            if new_tokens:
                logger.info("Subscribing %d dynamic gapper tokens", len(new_tokens))
                import time as _t
                _t.sleep(2)  # Wait for WebSocket stability
                try:
                    ticker.subscribe(new_tokens)
                    ticker.set_mode(ticker.MODE_FULL, new_tokens)
                    _t.sleep(1)
                except Exception as e:
                    logger.warning("Token subscribe failed: %s", e)

            print(f"\n  🔍 Dynamic scan: {len(new_gappers)} new gappers added")
            for g in new_gappers:
                print(f"     {g['symbol']}: gap {g.get('gap_pct',0):+.2f}%")

    threading.Thread(target=_dynamic_scan, daemon=True).start()

    def on_connect(ws, response):
        logger.info("Connected — %d tokens", len(tokens))
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_FULL, tokens)

    def on_reconnect(ws, attempts):
        logger.info("Reconnecting... attempt %d", attempts)

    def on_noreconnect(ws):
        logger.error("Max reconnects reached")

    import threading, time
    def _keepalive(ws_ref):
        while True:
            time.sleep(240)  # ping every 4 minutes
            try:
                if hasattr(ws_ref, '_ws') and ws_ref._ws:
                    ws_ref._ws.ping()
                    logger.debug("Keepalive ping sent")
            except: pass

    ticker = KiteTicker(API_KEY, ACCESS_TOKEN, reconnect=True, reconnect_max_tries=300, reconnect_max_delay=5)
    ticker.on_connect = on_connect
    ticker.on_ticks   = engine.on_tick
    ticker.on_close        = lambda ws,c,r: None
    ticker.on_error        = lambda ws,c,r: None
    ticker.on_reconnect    = on_reconnect
    ticker.on_noreconnect  = on_noreconnect

    logger.info("Live — 9:15 open capture → 9:35+ signals")
    try:
        ticker.connect(threaded=True)
        while True:
            now = datetime.now(IST)
            if now.hour >= 15 and now.minute >= 5:
                engine.print_summary()
                logger.info("3:05 PM — square off")
                save_daily_pnl(engine.risk, engine.signals,
                               market_direction, ctx.get("india_vix", 0) or 0)
                break
            time.sleep(10)
    except KeyboardInterrupt:
        engine.print_summary()


if __name__ == "__main__":
    main()
