"""
paper_trade_tracker.py — Forward paper-trade validation for investMITRA v27

Changes from v2:
  - Uses PAPER_TRADING constant for is_paper filter (not hardcoded True)
  - Historical rows with NULL metadata excluded from version-specific validation
  - Transaction rollback before retry on psycopg errors
  - Adds max_drawdown to criteria
  - Readiness label is "preliminary" — not a live-trading guarantee
"""
import os, psycopg2, statistics, logging
from decimal import Decimal
from datetime import datetime, date, timedelta, timezone
from dotenv import load_dotenv
load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

IST      = timezone(timedelta(hours=5, minutes=30))
NEON_URL = os.getenv("CC_POSTGRES_URL")

STRATEGY_VERSION = "v28"
IS_PAPER         = True   # match engine setting

def to_float(v, default=0.0) -> float:
    if v is None: return default
    try: return float(v)
    except: return default

def _migrate(conn):
    """Add metadata columns if missing. Uses ALTER ... ADD COLUMN IF NOT EXISTS."""
    cur = conn.cursor()
    for col, defn in [
        ("trade_id",         "VARCHAR(60)"),
        ("trade_status",     "VARCHAR(10) DEFAULT 'CLOSED'"),
        ("is_paper",         "BOOLEAN"),
        ("strategy_version", "VARCHAR(20)"),
    ]:
        cur.execute(
            f"ALTER TABLE investmitra.trade_log "
            f"ADD COLUMN IF NOT EXISTS {col} {defn}"
        )
    try:
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS trade_log_trade_id_uidx
            ON investmitra.trade_log(trade_id)
            WHERE trade_id IS NOT NULL
        """)
    except Exception: pass
    conn.commit()
    cur.close()

def get_paper_trades(days: int = 30,
                     version: str = STRATEGY_VERSION,
                     is_paper: bool = IS_PAPER) -> list:
    """
    Fetch completed paper trades for the specified strategy version.
    Historical rows with NULL metadata are excluded — they have not been
    classified and should not inflate or deflate version-specific stats.
    """
    conn = None
    try:
        conn = psycopg2.connect(NEON_URL, connect_timeout=15)
        _migrate(conn)
        cur = conn.cursor()
        cur.execute("""
            SELECT trade_date, symbol, direction,
                   entry_price, exit_price, quantity,
                   gross_pnl, net_pnl, outcome, hold_minutes,
                   true_gap_pct, rvol, final_score,
                   market_direction, session, atr, capital_deployed,
                   trade_status, is_paper, strategy_version, trade_id
            FROM investmitra.trade_log
            WHERE trade_date  >= CURRENT_DATE - INTERVAL %s
              AND trade_status  = 'CLOSED'
              AND is_paper      = %s
              AND strategy_version = %s
              AND exit_price IS NOT NULL
            ORDER BY trade_date, symbol
        """, (f"{days} days", is_paper, version))
        cols = ['date','symbol','direction','entry','exit','qty','gross','net',
                'outcome','hold_min','gap_pct','rvol','score','market','session',
                'atr','capital','trade_status','is_paper','strategy_version','trade_id']
        trades = []
        for row in cur.fetchall():
            t = dict(zip(cols, row))
            for k in ['entry','exit','qty','gross','net','hold_min',
                      'gap_pct','rvol','score','atr','capital']:
                t[k] = to_float(t.get(k))
            trades.append(t)
        cur.close(); conn.close()
        logger.info("Loaded %d completed %s trades (last %d days, %s)",
                    len(trades), "paper" if is_paper else "live", days, version)
        return trades
    except Exception as e:
        logger.error("get_paper_trades failed: %s", e)
        if conn:
            try: conn.rollback(); conn.close()
            except: pass
        return []

def validate(trades: list) -> dict:
    if not trades: return {}

    wins   = [t for t in trades if t['net'] > 0]
    losses = [t for t in trades if t['net'] <= 0]

    total_net     = sum(t['net'] for t in trades)
    win_rate      = len(wins) / len(trades) * 100
    avg_win       = sum(t['net'] for t in wins)   / len(wins)   if wins   else 0
    avg_loss      = sum(t['net'] for t in losses) / len(losses) if losses else 0
    expectancy    = (win_rate/100 * avg_win) + ((1-win_rate/100) * avg_loss)
    gross_wins    = sum(t['net'] for t in wins)
    gross_losses  = abs(sum(t['net'] for t in losses))
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else 999.0

    daily = {}
    for t in trades:
        d = str(t['date'])
        daily[d] = daily.get(d, 0) + t['net']

    pnl_vals  = list(daily.values())
    avg_daily = statistics.mean(pnl_vals) if pnl_vals else 0
    std_daily = statistics.stdev(pnl_vals) if len(pnl_vals) > 1 else 0
    sharpe    = avg_daily / std_daily * (252**0.5) if std_daily > 0 else 0

    equity = peak = max_dd = 0
    for d in sorted(daily):
        equity += daily[d]
        peak    = max(peak, equity)
        max_dd  = max(max_dd, peak - equity)

    by_session = {}
    for t in trades:
        s = t['session'] or 'unknown'
        b = by_session.setdefault(s, {'trades':0,'wins':0,'net':0})
        b['trades'] += 1
        b['wins']   += 1 if t['net'] > 0 else 0
        b['net']    += t['net']

    by_market = {}
    for t in trades:
        m = t['market'] or 'NEUTRAL'
        b = by_market.setdefault(m, {'trades':0,'wins':0,'net':0})
        b['trades'] += 1
        b['wins']   += 1 if t['net'] > 0 else 0
        b['net']    += t['net']

    high_rvol    = [t for t in trades if t['rvol'] >= 5]
    low_rvol     = [t for t in trades if t['rvol'] <  5]
    high_rvol_wr = len([t for t in high_rvol if t['net'] > 0]) / len(high_rvol) * 100 if high_rvol else 0
    low_rvol_wr  = len([t for t in low_rvol  if t['net'] > 0]) / len(low_rvol)  * 100 if low_rvol  else 0

    return dict(
        total_trades=len(trades), trading_days=len(daily),
        total_net=round(total_net,2), win_rate=round(win_rate,1),
        avg_win=round(avg_win,2), avg_loss=round(avg_loss,2),
        expectancy=round(expectancy,2), profit_factor=round(profit_factor,2),
        max_drawdown=round(max_dd,2), avg_daily=round(avg_daily,2),
        sharpe=round(sharpe,2), by_session=by_session, by_market=by_market,
        high_rvol_wr=round(high_rvol_wr,1), low_rvol_wr=round(low_rvol_wr,1),
        daily_pnl=daily,
    )

def check_preliminary(m: dict) -> dict:
    """
    Preliminary statistical checks. Passing all does NOT authorise live trading.
    See module docstring for additional requirements.
    """
    criteria = {
        'win_rate_45pct':      m.get('win_rate',0)       >= 45,
        'at_least_20_trades':  m.get('total_trades',0)   >= 20,
        'at_least_10_days':    m.get('trading_days',0)   >= 10,
        'positive_expectancy': m.get('expectancy',0)     >  0,
        'profit_factor_1_2':   m.get('profit_factor',0)  >  1.2,
        'positive_sharpe':     m.get('sharpe',0)         >  0,
        'avg_win_gt_avg_loss': abs(m.get('avg_win',0))   > abs(m.get('avg_loss',0)),
        'max_drawdown_lt_5k':  m.get('max_drawdown',9999)< 5000,
    }
    passed = sum(criteria.values())
    return {'criteria': criteria, 'passed': passed, 'total': len(criteria)}

def print_report(trades: list, days: int, version: str, is_paper: bool):
    m = validate(trades)
    if not m:
        mode = "paper" if is_paper else "live"
        print(f"No completed {mode} trades found for strategy {version}.")
        print("Historical rows with NULL metadata are excluded by design.")
        return

    go = check_preliminary(m)
    mode = "PAPER" if is_paper else "LIVE"

    print(f"\n{'='*63}")
    print(f"  {mode} TRADE VALIDATION — strategy {version}")
    print(f"  {m['total_trades']} trades | {m['trading_days']} days | last {days} days")
    print(f"  ⚠️  Preliminary check. See module docstring for live requirements.")
    print(f"{'='*63}")

    print(f"\n  CORE METRICS")
    print(f"  {'─'*53}")
    print(f"  Win rate:          {m['win_rate']:>6.1f}%")
    print(f"  Avg winner:        ₹{m['avg_win']:>9,.2f}")
    print(f"  Avg loser:         ₹{m['avg_loss']:>9,.2f}")
    print(f"  Expectancy:        ₹{m['expectancy']:>9,.2f}")
    print(f"  Profit factor:     {m['profit_factor']:>9.2f}")
    print(f"  Max drawdown:      ₹{m['max_drawdown']:>9,.2f}")
    print(f"  Total net:         ₹{m['total_net']:>9,.2f}")
    print(f"  Avg daily P&L:     ₹{m['avg_daily']:>9,.2f}")
    print(f"  Annualised Sharpe: {m['sharpe']:>9.2f}")

    print(f"\n  RVOL SPLIT")
    print(f"  RVOL ≥5x win rate: {m['high_rvol_wr']:.1f}%  |  "
          f"RVOL <5x: {m['low_rvol_wr']:.1f}%")

    print(f"\n  BY SESSION")
    for s, st in m['by_session'].items():
        wr = st['wins']/st['trades']*100 if st['trades'] else 0
        print(f"  {s:<12} n:{st['trades']:>3}  win:{wr:>5.1f}%  net:₹{st['net']:>8,.0f}")

    print(f"\n  BY MARKET")
    for mk, st in m['by_market'].items():
        wr = st['wins']/st['trades']*100 if st['trades'] else 0
        print(f"  {mk:<10} n:{st['trades']:>3}  win:{wr:>5.1f}%  net:₹{st['net']:>8,.0f}")

    print(f"\n  PRELIMINARY CHECKS ({go['passed']}/{go['total']} passed)")
    for k, passed in go['criteria'].items():
        print(f"  {'✅' if passed else '❌'} {k}")

    if go['passed'] == go['total']:
        print(f"\n  🟡 All preliminary checks passed.")
        print(f"     Additional requirements before live trading:")
        print(f"     • Acceptable drawdown across unseen market regimes")
        print(f"     • Broker reconciliation validated on live account")
        print(f"     • Forward validation (not in-sample)")
        print(f"     • Manual review of each trade category")
    else:
        print(f"\n  🔴 Continue paper trading — "
              f"{go['total']-go['passed']} check(s) not yet met.")

    print(f"\n  DAILY P&L")
    for d, pnl in sorted(m['daily_pnl'].items()):
        bar  = "█" * min(int(abs(pnl)/100), 28)
        sign = "+" if pnl >= 0 else "-"
        print(f"  {d}  {sign}₹{abs(pnl):>7,.0f}  {bar}")

    print(f"\n{'='*63}\n")

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--days",    type=int,  default=30)
    p.add_argument("--version", type=str,  default=STRATEGY_VERSION)
    p.add_argument("--live",    action="store_true", help="Validate live trades")
    args = p.parse_args()
    is_paper = not args.live
    trades = get_paper_trades(args.days, args.version, is_paper)
    print_report(trades, args.days, args.version, is_paper)
