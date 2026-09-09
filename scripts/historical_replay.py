"""
historical_replay.py — Signal-frequency and direction analysis for investMITRA

IMPORTANT LIMITATIONS (read before interpreting results):
  - Uses daily OHLC data only. Cannot reproduce intraday price sequence,
    so stop/target ordering within a day is unknown.
  - RVOL estimated from full-day volume — real signal fires at 9:40 AM
    when only ~7% of day volume has traded. Displayed RVOL is indicative only.
  - Scores are the latest available, not the score in effect on each trade date.
    Scores change weekly; earlier dates may have had different scores.
  - Entry/exit prices do not include partial exits, trailing stops or
    portfolio-level limits from the live engine.
  - When both stop and target are touched in a day, outcome is UNKNOWN
    (price sequence needed). These trades are excluded from win/loss counts
    and reported separately.
  - This output measures signal frequency and gap characteristics.
  - It does NOT replay the trading engine. Partial exits, trailing stops,
    intraday price confirmation and portfolio-level limits are absent.
  - Use paper_trade_tracker.py (reading live trade_log) for real validation.
  - This output measures signal frequency and gap characteristics,
    NOT live strategy P&L. Use paper_trade_tracker.py for real validation.
"""
import os, sys, psycopg2, logging, traceback
from decimal import Decimal
from datetime import datetime, date, timedelta, timezone
from dotenv import load_dotenv
load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

IST      = timezone(timedelta(hours=5, minutes=30))
NEON_URL = os.getenv("CC_POSTGRES_URL")

GAP_THRESH    = Decimal("0.003")   # 0.3% minimum true gap
SCORE_MIN     = 55
SLIPPAGE_PCT  = Decimal("0.001")   # 0.1% entry/exit slippage
BROKERAGE     = Decimal("40")      # realistic Zerodha intraday

def to_dec(v, default=Decimal("0")) -> Decimal:
    """Safely convert any numeric value to Decimal."""
    if v is None: return default
    if isinstance(v, Decimal): return v
    try: return Decimal(str(v))
    except: return default

def estimate_costs(entry: Decimal, qty: int, exit_price: Decimal) -> Decimal:
    ticket    = entry * qty
    brokerage = min(Decimal("20"), ticket * Decimal("0.0003")) * 2
    stt       = exit_price * qty * Decimal("0.00025")
    other     = ticket * Decimal("0.0001")
    return (brokerage + stt + other + Decimal("2")).quantize(Decimal("0.01"))

def get_universe(start_date: date, end_date: date) -> list:
    """
    Fetch complete score history for all stocks that appear in the period.
    Includes downgrades — a stock scoring 80 then downgraded to 30 will
    have both rows. The caller selects the score valid at each trade_date.
    Also fetches the last score before start_date (lookback) so the first
    days of the period have a valid score even if none was issued that day.
    """
    conn = psycopg2.connect(NEON_URL, connect_timeout=30)
    cur  = conn.cursor()
    # All scores in period (any score, not just >= SCORE_MIN)
    cur.execute("""
        SELECT cm.nse_symbol, cm.market_cap_category,
               ds.investmitra_score, ds.score_date, ds.sector
        FROM investmitra.daily_scores ds
        JOIN investmitra.company_master cm ON ds.isin = cm.isin
        WHERE ds.score_date BETWEEN %s AND %s
          AND cm.nse_symbol IS NOT NULL
        UNION ALL
        -- Last score before period start for each symbol (lookback row)
        SELECT nse_symbol, market_cap_category, investmitra_score, score_date, sector
        FROM (
            SELECT cm.nse_symbol, cm.market_cap_category,
                   ds.investmitra_score, ds.score_date, ds.sector,
                   ROW_NUMBER() OVER (PARTITION BY cm.nse_symbol
                                      ORDER BY ds.score_date DESC) AS rn
            FROM investmitra.daily_scores ds
            JOIN investmitra.company_master cm ON ds.isin = cm.isin
            WHERE ds.score_date < %s
              AND cm.nse_symbol IS NOT NULL
        ) sub
        WHERE rn = 1
        ORDER BY score_date, nse_symbol
    """, (start_date, end_date, start_date))
    rows = cur.fetchall()
    cur.close(); conn.close()
    logger.info("Universe: %d score rows (incl downgrades) for %s–%s",
                len(rows), start_date, end_date)
    return rows

def get_daily_ohlcv(symbol: str, start_date: date, end_date: date) -> list:
    """OHLCV with lookback for ATR and prev_close. Uses Decimal from DB."""
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("""
        SELECT ep.trade_date,
               ep.open, ep.high, ep.low, ep.close, ep.volume,
               LAG(ep.close) OVER (ORDER BY ep.trade_date) AS prev_close,
               AVG(ep.volume) OVER (ORDER BY ep.trade_date
                   ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)   AS avg_vol_20,
               AVG(ep.high - ep.low) OVER (ORDER BY ep.trade_date
                   ROWS BETWEEN 14 PRECEDING AND 1 PRECEDING)   AS atr14
        FROM investmitra.equity_prices ep
        JOIN investmitra.company_master cm ON ep.isin = cm.isin
        WHERE cm.nse_symbol = %s
          AND ep.trade_date BETWEEN %s AND %s
        ORDER BY ep.trade_date
    """, (symbol, start_date - timedelta(days=30), end_date))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return rows

def analyse_day(row: tuple, score: Decimal, cap: str,
                score_min: Decimal = Decimal(str(SCORE_MIN))) -> dict | None:
    """
    Analyse one trading day for signal presence and indicative outcome.
    Returns None if no signal would have fired.
    Does NOT claim to reproduce live engine behaviour.
    """
    trade_date, open_p, high, low, close, volume, prev_close, avg_vol_20, atr14 = row
    open_p    = to_dec(open_p)
    high      = to_dec(high)
    low       = to_dec(low)
    close     = to_dec(close)
    prev_close = to_dec(prev_close)
    avg_vol_20 = to_dec(avg_vol_20, Decimal("1"))
    atr14     = to_dec(atr14)
    volume    = int(volume or 0)

    # Apply score eligibility FIRST — score must meet threshold on this date
    if score < score_min:
        return None

    if not prev_close or not open_p or avg_vol_20 == 0:
        return None

    true_gap = (open_p - prev_close) / prev_close
    if abs(true_gap) < GAP_THRESH:
        return None

    # RVOL NOTE: full-day volume used — real signal fires at ~9:40 AM
    # with roughly 7% of daily volume. Displayed RVOL is informational only.
    full_day_rvol = volume / float(avg_vol_20) if avg_vol_20 > 0 else 0
    indicative_rvol_note = f"full-day {full_day_rvol:.1f}x (signal-time ~{full_day_rvol*0.07:.1f}x est)"

    direction = "LONG" if true_gap > 0 else "SHORT"
    atr = atr14 or (high - low) or open_p * Decimal("0.01")

    if direction == "LONG":
        entry  = open_p * (1 + SLIPPAGE_PCT)
        stop   = entry - atr * Decimal("1.5")
        target = entry + atr * Decimal("1.5")
    else:
        entry  = open_p * (1 - SLIPPAGE_PCT)
        stop   = entry + atr * Decimal("1.5")
        target = entry - atr * Decimal("1.5")

    stop_dist = abs(entry - stop)
    if stop_dist == 0:
        return None
    size = min(int(2000 / float(stop_dist)), int(25000 / float(entry)))
    if size <= 0:
        return None

    costs = estimate_costs(entry, size, target)
    gross = Decimal("0")
    outcome = "UNKNOWN"

    if direction == "LONG":
        stop_hit   = low  <= stop
        target_hit = high >= target
    else:
        stop_hit   = high >= stop
        target_hit = low  <= target

    if target_hit and not stop_hit:
        outcome = "TARGET"
        exit_p  = target * (1 - SLIPPAGE_PCT) if direction == "LONG" else target * (1 + SLIPPAGE_PCT)
        gross   = (exit_p - entry) * size if direction == "LONG" else (entry - exit_p) * size
    elif stop_hit and not target_hit:
        outcome = "STOP"
        exit_p  = stop
        gross   = (exit_p - entry) * size if direction == "LONG" else (entry - exit_p) * size
    elif not stop_hit and not target_hit:
        outcome = "TIME_EXIT"
        exit_p  = close
        gross   = (exit_p - entry) * size if direction == "LONG" else (entry - exit_p) * size
    else:
        # Both touched — sequence unknown, exclude from P&L
        outcome = "BOTH_TOUCHED_UNKNOWN"
        exit_p  = close
        gross   = Decimal("0")

    net = gross - costs if outcome != "BOTH_TOUCHED_UNKNOWN" else Decimal("0")

    return {
        "date":       trade_date,
        "symbol":     None,  # filled by caller
        "cap":        cap,
        "score":      float(score),
        "direction":  direction,
        "gap_pct":    round(float(true_gap) * 100, 2),
        "rvol_note":  indicative_rvol_note,
        "entry":      float(entry),
        "stop":       float(stop),
        "target":     float(target),
        "size":       size,
        "atr":        float(atr),
        "gross":      float(gross),
        "costs":      float(costs),
        "net":        float(net),
        "outcome":    outcome,
        "countable":  outcome not in ("UNKNOWN", "BOTH_TOUCHED_UNKNOWN"),
        "win":        float(net) > 0 and outcome not in ("UNKNOWN", "BOTH_TOUCHED_UNKNOWN"),
    }

def run_replay(start_date: date = None, end_date: date = None, days: int = 60):
    if not end_date:
        end_date   = date.today() - timedelta(days=1)
    if not start_date:
        start_date = end_date - timedelta(days=days)

    print(f"\n{'='*65}")
    print(f"  investMITRA SIGNAL-FREQUENCY ANALYSIS (not P&L replay)")
    print(f"  Period: {start_date} to {end_date}")
    print(f"  Gap threshold: {float(GAP_THRESH)*100:.1f}%  Score min: {SCORE_MIN}")
    print(f"  ⚠️  See module docstring for limitations before interpreting.")
    print(f"{'='*65}\n")

    scored_rows = get_universe(start_date, end_date)
    # Group by symbol — use score valid on each trade_date
    from collections import defaultdict
    sym_scores = defaultdict(list)
    for sym, cap, score, score_date, sector in scored_rows:
        sym_scores[sym].append((score_date, to_dec(score), cap))

    all_trades = []
    errors     = 0
    unknown    = 0

    for sym, score_history in sym_scores.items():
        score_history.sort()
        cap = score_history[-1][2]
        try:
            prices = get_daily_ohlcv(sym, start_date, end_date)
            for row in prices:
                trade_date = row[0]
                if trade_date < start_date:
                    continue
                # Use score valid on this date (last score_date <= trade_date)
                valid_scores = [(sd, sc, cp) for sd, sc, cp in score_history if sd <= trade_date]
                if not valid_scores:
                    continue
                _, score, cap = valid_scores[-1]
                result = analyse_day(row, score, cap,
                                        score_min=Decimal(str(SCORE_MIN)))
                if result:
                    result["symbol"] = sym
                    if result["outcome"] == "BOTH_TOUCHED_UNKNOWN":
                        unknown += 1
                    all_trades.append(result)
        except Exception as e:
            errors += 1
            logger.warning("Error for %s: %s", sym, e)
            logger.debug(traceback.format_exc())

    countable = [t for t in all_trades if t["countable"]]
    wins      = [t for t in countable if t["win"]]
    losses    = [t for t in countable if not t["win"]]

    print(f"  SIGNAL FREQUENCY")
    print(f"  {'─'*55}")
    print(f"  Signals found:       {len(all_trades):>6}")
    print(f"  Countable outcomes:  {len(countable):>6}  (stop XOR target)")
    print(f"  Unknown (both hit):  {unknown:>6}  (excluded from stats)")
    print(f"  Data errors:         {errors:>6}")

    if countable:
        total_net = sum(t["net"] for t in countable)
        win_rate  = len(wins) / len(countable) * 100
        avg_win   = sum(t["net"] for t in wins)   / len(wins)   if wins   else 0
        avg_loss  = sum(t["net"] for t in losses) / len(losses) if losses else 0
        expectancy = (win_rate/100 * avg_win) + ((1-win_rate/100) * avg_loss)

        print(f"\n  INDICATIVE STATS (see limitations)")
        print(f"  {'─'*55}")
        print(f"  Win rate:         {win_rate:>6.1f}%")
        print(f"  Avg winner:       ₹{avg_win:>8,.2f}")
        print(f"  Avg loser:        ₹{avg_loss:>8,.2f}")
        print(f"  Expectancy/trade: ₹{expectancy:>8,.2f}")
        print(f"  Total net (est):  ₹{total_net:>8,.2f}")
        print(f"  ⚠️  These numbers exclude partial exits, trailing stops,")
        print(f"      portfolio limits and correct RVOL filtering.")

    print(f"\n{'='*65}\n")

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--days",  type=int, default=60)
    p.add_argument("--start", type=str, default=None)
    p.add_argument("--end",   type=str, default=None)
    args = p.parse_args()
    start = date.fromisoformat(args.start) if args.start else None
    end   = date.fromisoformat(args.end)   if args.end   else None
    run_replay(start, end, args.days)
