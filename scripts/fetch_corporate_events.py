"""
investMITRA — Corporate Events Fetcher
Fetches NSE corporate calendar daily and flags our top picks
that have upcoming board meetings, results, dividends etc.

Events tracked:
  - Quarterly Results (most important)
  - Dividend announcements
  - Fund raising (dilution risk)
  - Board meetings
  - AGM/EGM
  - Bonus/splits

Flags in Grafana:
  ⚠️ Results today/tomorrow — avoid intraday
  📅 Dividend ex-date — hold for dividend
  🚨 Fund raise — dilution risk

Run: python scripts/fetch_corporate_events.py
"""
from __future__ import annotations
import logging, os
from datetime import date, datetime, timedelta, timezone
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

# Event categories
RESULTS_KEYWORDS  = ["quarterly result", "financial result", "q1", "q2", "q3", "q4",
                     "annual result", "half year"]
DIVIDEND_KEYWORDS = ["dividend", "interim dividend", "final dividend"]
DILUTION_KEYWORDS = ["fund raising", "rights issue", "preferential allotment", "qip",
                     "buyback"]
BONUS_KEYWORDS    = ["bonus", "stock split", "sub-division"]


def fetch_nse_events(days_ahead: int = 7) -> list[dict]:
    """Fetch NSE corporate calendar for next N days."""
    session = requests.Session()
    session.headers.update(NSE_HEADERS)

    try:
        # First hit homepage to get cookies
        session.get("https://www.nseindia.com", timeout=10)

        r = session.get("https://www.nseindia.com/api/event-calendar",
                        timeout=15)
        if r.status_code != 200:
            logger.error("NSE calendar HTTP %d", r.status_code)
            return []

        data   = r.json()
        today  = datetime.now(IST).date()
        cutoff = today + timedelta(days=days_ahead)

        events = []
        for item in data:
            try:
                event_date = datetime.strptime(item["date"], "%d-%b-%Y").date()
            except:
                continue

            if event_date < today or event_date > cutoff:
                continue

            purpose = (item.get("purpose", "") or "").lower()
            desc    = (item.get("bm_desc", "") or "").lower()
            full_text = purpose + " " + desc

            # Categorize event
            if any(k in full_text for k in RESULTS_KEYWORDS):
                category = "RESULTS"
            elif any(k in full_text for k in DIVIDEND_KEYWORDS):
                category = "DIVIDEND"
            elif any(k in full_text for k in DILUTION_KEYWORDS):
                category = "DILUTION"
            elif any(k in full_text for k in BONUS_KEYWORDS):
                category = "BONUS"
            else:
                category = "BOARD_MEETING"

            events.append({
                "symbol":     item.get("symbol", ""),
                "company":    item.get("company", ""),
                "event_date": event_date,
                "purpose":    item.get("purpose", ""),
                "category":   category,
                "description":item.get("bm_desc", "")[:300],
                "days_away":  (event_date - today).days,
            })

        logger.info("Fetched %d events (next %d days)", len(events), days_ahead)
        return events

    except Exception as e:
        logger.error("NSE calendar fetch failed: %s", e)
        return []


def ensure_table():
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = True
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS investmitra.corporate_events (
            id          SERIAL PRIMARY KEY,
            symbol      VARCHAR(20),
            isin        VARCHAR(12),
            company     VARCHAR(200),
            event_date  DATE NOT NULL,
            purpose     VARCHAR(200),
            category    VARCHAR(30),
            description TEXT,
            days_away   INTEGER,
            fetched_at  TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (symbol, event_date, purpose)
        )
    """)
    cur.close(); conn.close()


def get_symbol_isin_map() -> dict[str, str]:
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("SELECT UPPER(nse_symbol), isin FROM investmitra.company_master WHERE nse_symbol IS NOT NULL")
    result = {r[0]: r[1] for r in cur.fetchall()}
    cur.close(); conn.close()
    return result


def save_events(events: list[dict], sym_isin: dict):
    if not events: return
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = False
    cur  = conn.cursor()

    rows = [(
        e["symbol"], sym_isin.get(e["symbol"].upper()),
        e["company"], e["event_date"], e["purpose"],
        e["category"], e["description"], e["days_away"]
    ) for e in events]

    execute_values(cur, """
        INSERT INTO investmitra.corporate_events
            (symbol, isin, company, event_date, purpose, category, description, days_away)
        VALUES %s
        ON CONFLICT (symbol, event_date, purpose) DO UPDATE SET
            days_away  = EXCLUDED.days_away,
            fetched_at = NOW()
    """, rows, page_size=100)

    conn.commit(); cur.close(); conn.close()
    logger.info("Saved %d events to Neon", len(rows))


def print_alerts(events: list[dict]):
    today    = datetime.now(IST).date()
    tomorrow = today + timedelta(days=1)

    results_today    = [e for e in events if e["category"] == "RESULTS" and e["event_date"] == today]
    results_tomorrow = [e for e in events if e["category"] == "RESULTS" and e["event_date"] == tomorrow]
    dividends        = [e for e in events if e["category"] == "DIVIDEND" and e["days_away"] <= 3]
    dilutions        = [e for e in events if e["category"] == "DILUTION" and e["days_away"] <= 5]

    print(f"\n{'='*65}")
    print(f"CORPORATE CALENDAR ALERTS — {today}")
    print(f"{'='*65}")

    if results_today:
        print(f"\n🚨 RESULTS TODAY ({len(results_today)}) — High volatility expected:")
        for e in results_today:
            print(f"   {e['symbol']:<15} {e['company'][:35]}")

    if results_tomorrow:
        print(f"\n⚠️  RESULTS TOMORROW ({len(results_tomorrow)}) — Avoid overnight hold:")
        for e in results_tomorrow:
            print(f"   {e['symbol']:<15} {e['company'][:35]}")

    if dividends:
        print(f"\n💰 DIVIDEND ({len(dividends)}) — Ex-date approaching:")
        for e in dividends:
            print(f"   {e['symbol']:<15} {e['purpose'][:40]} — {e['event_date']}")

    if dilutions:
        print(f"\n🔻 DILUTION RISK ({len(dilutions)}) — Fund raising:")
        for e in dilutions:
            print(f"   {e['symbol']:<15} {e['purpose'][:40]} — {e['event_date']}")

    # Cross-reference with our top picks
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("""
        SELECT tp.company_name, tp.nse_symbol, tp.investmitra_score,
               ce.category, ce.event_date, ce.purpose
        FROM investmitra.top_picks tp
        JOIN investmitra.corporate_events ce ON UPPER(tp.nse_symbol) = UPPER(ce.symbol)
        WHERE tp.pick_date = (SELECT MAX(pick_date) FROM investmitra.top_picks)
          AND ce.event_date <= CURRENT_DATE + INTERVAL '5 days'
        ORDER BY ce.event_date
    """)
    overlap = cur.fetchall()
    cur.close(); conn.close()

    if overlap:
        print(f"\n{'='*65}")
        print(f"⚠️  TOP PICKS WITH UPCOMING EVENTS:")
        print(f"{'='*65}")
        for r in overlap:
            flag = "🚨" if r[3] == "RESULTS" else "💰" if r[3] == "DIVIDEND" else "⚠️"
            print(f"   {flag} {r[1]:<12} Score:{float(r[2]):.1f} | {r[3]} on {r[4]} | {str(r[5])[:40]}")

    print(f"\n{'='*65}")


def main():
    ensure_table()
    sym_isin = get_symbol_isin_map()
    events   = fetch_nse_events(days_ahead=7)

    if events:
        save_events(events, sym_isin)
        print_alerts(events)
    else:
        logger.warning("No events fetched")


if __name__ == "__main__":
    main()
