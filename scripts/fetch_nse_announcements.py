"""
investMITRA — NSE Announcements Fetcher
Fetches real-time NSE corporate announcements every 30 min during market hours.
Cross-references with our top picks and intraday watchlist.
Stores in Neon for Grafana and intraday alerts.

Run standalone: python scripts/fetch_nse_announcements.py
Run in loop:    python scripts/fetch_nse_announcements.py --loop
"""
from __future__ import annotations
import argparse, logging, os, time
from datetime import datetime, date, timedelta, timezone
import requests
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv
load_dotenv('.env.prod')

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NEON_URL = os.getenv("CC_POSTGRES_URL")
IST      = timezone(timedelta(hours=5, minutes=30))

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "application/json",
    "Referer":    "https://www.nseindia.com",
}

# High-importance announcement types
HIGH_IMPORTANCE = [
    "quarterly result", "financial result", "annual result",
    "board meeting", "dividend", "buyback", "bonus",
    "merger", "acquisition", "restructuring",
    "analyst", "institutional investor meet",
    "credit rating", "fund raising", "rights issue",
    "resignation of director", "appointment",
]

MARKET_SENSITIVE = [
    "quarterly result", "financial result", "board meeting outcome",
    "dividend", "buyback", "merger", "acquisition",
]


def get_nse_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    try:
        session.get("https://www.nseindia.com", timeout=10)
    except: pass
    return session


def fetch_announcements(session: requests.Session) -> list[dict]:
    try:
        r = session.get(
            "https://www.nseindia.com/api/corporate-announcements?index=equities",
            timeout=15
        )
        if r.status_code != 200:
            logger.warning("NSE announcements HTTP %d", r.status_code)
            return []

        data = r.json()
        if not isinstance(data, list):
            return []

        announcements = []
        for item in data:
            ann_type = (item.get("desc", "") or item.get("subject", "") or "").strip()
            symbol   = (item.get("symbol", "") or "").strip()
            ann_dt   = item.get("an_dt", "")

            # Parse datetime
            try:
                ann_time = datetime.strptime(ann_dt, "%d-%b-%Y %H:%M:%S")
                ann_time = ann_time.replace(tzinfo=IST)
            except:
                ann_time = None

            # Check importance
            ann_lower    = ann_type.lower()
            is_important = any(k in ann_lower for k in HIGH_IMPORTANCE)
            is_sensitive = any(k in ann_lower for k in MARKET_SENSITIVE)

            announcements.append({
                "symbol":       symbol,
                "ann_datetime": ann_time,
                "ann_type":     ann_type,
                "file_url":     item.get("attchmntFile", ""),
                "file_size":    item.get("attFileSize", ""),
                "is_important": is_important,
                "is_sensitive": is_sensitive,
            })

        logger.info("Fetched %d announcements", len(announcements))
        return announcements

    except Exception as e:
        logger.error("Fetch failed: %s", e)
        return []


def ensure_table():
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = True
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS investmitra.nse_announcements (
            id           SERIAL PRIMARY KEY,
            symbol       VARCHAR(20),
            isin         VARCHAR(12),
            ann_datetime TIMESTAMPTZ,
            ann_type     VARCHAR(200),
            file_url     TEXT,
            is_important BOOLEAN DEFAULT FALSE,
            is_sensitive BOOLEAN DEFAULT FALSE,
            fetched_at   TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (symbol, ann_datetime, ann_type)
        )
    """)
    cur.close(); conn.close()


def get_symbol_maps() -> tuple[dict, set]:
    """Returns (symbol->isin map, set of watchlist symbols)."""
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()

    # Symbol to ISIN
    cur.execute("SELECT UPPER(nse_symbol), isin FROM investmitra.company_master WHERE nse_symbol IS NOT NULL")
    sym_isin = {r[0]: r[1] for r in cur.fetchall()}

    # Today's top picks watchlist
    cur.execute("""
        SELECT UPPER(nse_symbol) FROM investmitra.top_picks
        WHERE pick_date = (SELECT MAX(pick_date) FROM investmitra.top_picks)
        UNION
        SELECT UPPER(nse_symbol) FROM investmitra.daily_scores ds
        JOIN investmitra.company_master cm ON ds.isin = cm.isin
        WHERE ds.score_date = (SELECT MAX(score_date) FROM investmitra.daily_scores)
          AND ds.signal IN ('Strong Buy', 'Buy')
          AND ds.investmitra_score >= 65
    """)
    watchlist = {r[0] for r in cur.fetchall() if r[0]}

    cur.close(); conn.close()
    return sym_isin, watchlist


def save_announcements(announcements: list[dict], sym_isin: dict):
    if not announcements: return
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = False
    cur  = conn.cursor()

    rows = [(
        a["symbol"],
        sym_isin.get(a["symbol"].upper()),
        a["ann_datetime"],
        a["ann_type"],
        a["file_url"],
        a["is_important"],
        a["is_sensitive"],
    ) for a in announcements if a["ann_datetime"]]

    execute_values(cur, """
        INSERT INTO investmitra.nse_announcements
            (symbol, isin, ann_datetime, ann_type, file_url, is_important, is_sensitive)
        VALUES %s
        ON CONFLICT (symbol, ann_datetime, ann_type) DO NOTHING
    """, rows, page_size=50)

    conn.commit(); cur.close(); conn.close()


def print_alerts(announcements: list[dict], watchlist: set):
    now = datetime.now(IST)

    # Filter recent (last 2 hours)
    recent = [a for a in announcements
              if a["ann_datetime"] and
              (now - a["ann_datetime"]).total_seconds() < 7200]

    # Cross-reference with watchlist
    watchlist_alerts = [a for a in recent
                       if a["symbol"].upper() in watchlist]

    if watchlist_alerts:
        print(f"\n{'='*65}")
        print(f"🚨 WATCHLIST ALERTS — NSE Announcements")
        print(f"{'='*65}")
        for a in watchlist_alerts:
            sensitivity = "🔴 MARKET SENSITIVE" if a["is_sensitive"] else "⚠️ Important" if a["is_important"] else "ℹ️ Info"
            print(f"\n  {sensitivity}")
            print(f"  Symbol:  {a['symbol']}")
            print(f"  Type:    {a['ann_type']}")
            print(f"  Time:    {a['ann_datetime'].strftime('%H:%M:%S') if a['ann_datetime'] else 'N/A'}")
            if a['file_url']:
                print(f"  File:    {a['file_url'][:60]}...")

    # Show all important announcements
    important = [a for a in recent if a["is_important"]]
    if important:
        print(f"\n{'='*65}")
        print(f"📋 IMPORTANT ANNOUNCEMENTS (last 2 hrs) — {len(important)}")
        print(f"{'='*65}")
        print(f"  {'Symbol':<15} {'Time':<10} {'Type'}")
        print(f"  {'─'*60}")
        for a in important[:20]:
            t = a["ann_datetime"].strftime("%H:%M") if a["ann_datetime"] else "?"
            print(f"  {a['symbol']:<15} {t:<10} {a['ann_type'][:35]}")

    if not watchlist_alerts and not important:
        print(f"\n  No important announcements in last 2 hours.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true",
                        help="Run every 30 min during market hours")
    args = parser.parse_args()

    ensure_table()
    sym_isin, watchlist = get_symbol_maps()
    logger.info("Watchlist: %d symbols", len(watchlist))

    if args.loop:
        logger.info("Running in loop mode — every 30 min")
        while True:
            now = datetime.now(IST)
            # Only run during market hours 9:00 AM - 4:00 PM
            if 9 <= now.hour < 16:
                session = get_nse_session()
                announcements = fetch_announcements(session)
                save_announcements(announcements, sym_isin)
                print_alerts(announcements, watchlist)
                logger.info("Next check in 30 minutes...")
                time.sleep(1800)
            else:
                logger.info("Market closed — sleeping 1 hour")
                time.sleep(3600)
    else:
        session       = get_nse_session()
        announcements = fetch_announcements(session)
        save_announcements(announcements, sym_isin)
        print_alerts(announcements, watchlist)


if __name__ == "__main__":
    main()
