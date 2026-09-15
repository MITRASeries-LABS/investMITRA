"""
auto_paper_summary.py — Daily P&L summary from auto_paper SQLite journal
Run after market close: python scripts/auto_paper_summary.py
"""
import sqlite3, json, os, sys
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

def summarise(db_path='data/execution_auto_paper.sqlite3', target_date=None):
    if not os.path.exists(db_path):
        print("Journal not found:", db_path)
        return

    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()
    cur.execute("SELECT * FROM state")
    row = cur.fetchone()
    conn.close()

    if not row:
        print("No state in journal.")
        return

    state = json.loads(row[1])
    day   = state.get('day', '?')

    if target_date and day != target_date:
        # Check history
        conn = sqlite3.connect(db_path)
        cur  = conn.cursor()
        cur.execute("SELECT data FROM history WHERE data LIKE ?", (f'%"day": "{target_date}"%',))
        row = cur.fetchone()
        conn.close()
        if row:
            state = json.loads(row[0])
            day   = target_date
        else:
            print(f"No data for {target_date}")
            return

    trades = state.get('trades', {})

    print(f"\n{'='*60}")
    print(f"  AUTO-PAPER SUMMARY — {day}")
    print(f"  Mode: {state.get('mode','?')} | Account: {state.get('account','?')}")
    print(f"{'='*60}")

    total_gross  = 0
    total_costs  = 0
    wins = losses = 0

    rows = []
    for sym, t in trades.items():
        sig       = t.get('signal', {})
        orders    = t.get('orders', [])
        direction = sig.get('direction', 'LONG')
        sign      = t.get('sign', 1)

        # Entry fills
        entry_qty  = sum(o.get('filled',0) for o in orders if o['kind']=='ENTRY')
        entry_val  = sum(o.get('filled',0)*o.get('average',0) for o in orders if o['kind']=='ENTRY')
        entry_avg  = entry_val / entry_qty if entry_qty else 0

        # Exit fills (EXIT or STOP)
        exit_qty   = sum(o.get('filled',0) for o in orders if o['kind'] in ('EXIT','STOP') and o.get('filled',0)>0)
        exit_val   = sum(o.get('filled',0)*o.get('average',0) for o in orders if o['kind'] in ('EXIT','STOP') and o.get('filled',0)>0)
        exit_avg   = exit_val / exit_qty if exit_qty else 0

        if entry_qty == 0:
            status = "NOT FILLED"
            gross  = 0
        elif t.get('closed_at'):
            gross  = (exit_val - entry_val) * sign
            status = t.get('exit_reason', 'CLOSED')
        else:
            gross  = 0
            status = "OPEN"

        costs    = sig.get('estimated_costs', 80)
        net      = gross - costs
        total_gross += gross
        total_costs += costs if entry_qty > 0 else 0

        if entry_qty > 0 and t.get('closed_at'):
            if net > 0: wins += 1
            else:       losses += 1

        rows.append((sym, direction, entry_qty, entry_avg, exit_avg, gross, net, status))

    print(f"\n  {'Symbol':<12} {'Dir':<5} {'Qty':>4} {'Entry':>8} {'Exit':>8} {'Gross':>8} {'Net':>8}  Status")
    print(f"  {'─'*75}")
    for sym, direction, qty, entry_avg, exit_avg, gross, net, status in rows:
        if qty == 0:
            print(f"  {sym:<12} {direction:<5} {'--':>4} {'--':>8} {'--':>8} {'--':>8} {'--':>8}  {status}")
        else:
            print(f"  {sym:<12} {direction:<5} {qty:>4} {entry_avg:>8.2f} {exit_avg:>8.2f} "
                  f"{gross:>+8.0f} {net:>+8.0f}  {status}")
        total_gross += 0  # already counted above

    total_net = total_gross - total_costs
    print(f"\n  {'─'*75}")
    print(f"  Gross: ₹{total_gross:+.2f} | Charges: ₹{total_costs:.2f} | NET: ₹{total_net:+.2f}")
    print(f"  Wins: {wins} | Losses: {losses} | Win rate: {wins/(wins+losses)*100:.0f}%" if wins+losses else "  No completed trades.")
    print(f"\n  Budget used: ₹{state.get('tickets',0):.2f} of ₹25,000")
    print(f"  Halt: {state.get('halt','none') or 'none'}")
    print(f"{'='*60}\n")

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--date', default=None)
    p.add_argument('--db',   default='data/execution_auto_paper.sqlite3')
    args = p.parse_args()
    summarise(args.db, args.date)
