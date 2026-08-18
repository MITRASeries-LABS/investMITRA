"""
investMITRA — SEBI RSS Feed Fetcher
Fetches SEBI orders, circulars, and press releases.
Cross-references with our watchlist for regulatory risk flags.

SEBI RSS: https://www.sebi.gov.in/sebirss.xml

Types tracked:
  - Orders against companies (enforcement)
  - Circulars (new regulations)
  - Press releases (policy changes)
  - Recovery certificates (payment demands)

Run: python scripts/fetch_sebi_rss.py
"""
from __future__ import annotations
import logging, os, warnings
from datetime import datetime, timedelta, timezone
import requests
import psycopg2
from psycopg2.extras import execute_values
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from dotenv import load_dotenv
load_dotenv('.env.prod')

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NEON_URL = os.getenv("CC_POSTGRES_URL")
IST      = timezone(timedelta(hours=5, minutes=30))
SEBI_RSS = "https://www.sebi.gov.in/sebirss.xml"

# Keywords indicating enforcement action against a company
ENFORCEMENT_KEYWORDS = [
    "order", "penalty", "demand", "recovery", "certificate",
    "enquiry", "proceedings", "direction", "prohibition",
    "debarred", "suspended", "cancelled", "show cause"
]

CIRCULAR_KEYWORDS = [
    "circular", "regulation", "amendment", "guidelines",
    "framework", "policy", "notification"
]

PRESS_KEYWORDS = [
    "press release", "press note", "consultation paper"
]


def fetch_sebi_rss() -> list[dict]:
    try:
        r = requests.get(SEBI_RSS, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            logger.error("SEBI RSS HTTP %d", r.status_code)
            return []

        soup  = BeautifulSoup(r.text, 'lxml')
        items = soup.find_all('item')
        logger.info("Fetched %d SEBI items", len(items))

        results = []
        for item in items:
            title   = item.find('title')
            pubdate = item.find('pubdate')
            link    = item.find('link')
            desc    = item.find('description')

            title_text = title.text.strip() if title else ""
            link_url   = link.text.strip() if link else ""
            desc_text  = desc.text.strip() if desc else ""

            # Parse date
            pub_dt = None
            if pubdate:
                try:
                    pub_dt = datetime.strptime(
                        pubdate.text.strip().replace("+0530","").strip(),
                        "%d %b, %Y"
                    ).replace(tzinfo=IST)
                except:
                    pass

            # Categorize
            title_lower = title_text.lower()
            if any(k in title_lower for k in ENFORCEMENT_KEYWORDS):
                category = "ENFORCEMENT"
            elif any(k in title_lower for k in CIRCULAR_KEYWORDS):
                category = "CIRCULAR"
            elif any(k in title_lower for k in PRESS_KEYWORDS):
                category = "PRESS_RELEASE"
            else:
                category = "OTHER"

            # Extract company names mentioned (rough extraction)
            # Look for "in the matter of X" or "of X Ltd"
            company_hint = ""
            for phrase in ["in the matter of ", "respect of ", "against "]:
                if phrase in title_lower:
                    idx = title_lower.find(phrase) + len(phrase)
                    company_hint = title_text[idx:idx+50].strip()
                    break

            results.append({
                "title":        title_text,
                "pub_date":     pub_dt,
                "link":         link_url,
                "category":     category,
                "company_hint": company_hint,
                "is_enforcement": category == "ENFORCEMENT",
            })

        return results

    except Exception as e:
        logger.error("SEBI RSS fetch failed: %s", e)
        return []


def ensure_table():
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = True
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS investmitra.sebi_updates (
            id              SERIAL PRIMARY KEY,
            title           TEXT,
            pub_date        DATE,
            link            TEXT,
            category        VARCHAR(30),
            company_hint    VARCHAR(200),
            is_enforcement  BOOLEAN DEFAULT FALSE,
            fetched_at      TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (title, pub_date)
        )
    """)
    cur.close(); conn.close()


def save_updates(items: list[dict]):
    if not items: return
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    conn.autocommit = False
    cur  = conn.cursor()

    rows = [(
        i["title"], i["pub_date"].date() if i["pub_date"] else None,
        i["link"], i["category"], i["company_hint"], i["is_enforcement"]
    ) for i in items]

    execute_values(cur, """
        INSERT INTO investmitra.sebi_updates
            (title, pub_date, link, category, company_hint, is_enforcement)
        VALUES %s
        ON CONFLICT (title, pub_date) DO NOTHING
    """, rows, page_size=50)

    conn.commit(); cur.close(); conn.close()
    logger.info("Saved %d SEBI updates", len(rows))


def get_watchlist_symbols() -> set[str]:
    conn = psycopg2.connect(NEON_URL, connect_timeout=15)
    cur  = conn.cursor()
    cur.execute("""
        SELECT UPPER(nse_symbol) FROM investmitra.top_picks
        WHERE pick_date = (SELECT MAX(pick_date) FROM investmitra.top_picks)
        UNION
        SELECT UPPER(cm.company_name) FROM investmitra.top_picks tp
        JOIN investmitra.company_master cm ON tp.isin = cm.isin
        WHERE tp.pick_date = (SELECT MAX(pick_date) FROM investmitra.top_picks)
    """)
    result = {r[0] for r in cur.fetchall() if r[0]}
    cur.close(); conn.close()
    return result


def print_summary(items: list[dict], watchlist: set):
    print(f"\n{'='*65}")
    print(f"SEBI UPDATES — {datetime.now(IST).strftime('%d %b %Y')}")
    print(f"{'='*65}")

    enforcement = [i for i in items if i["is_enforcement"]]
    circulars   = [i for i in items if i["category"] == "CIRCULAR"]

    # Check if any watchlist company is mentioned
    watchlist_hits = []
    for item in items:
        hint = item["company_hint"].upper()
        for sym in watchlist:
            if sym and len(sym) > 3 and sym in hint:
                watchlist_hits.append((sym, item))

    if watchlist_hits:
        print(f"\n🚨 WATCHLIST COMPANY IN SEBI ACTION:")
        for sym, item in watchlist_hits:
            print(f"  {sym}: {item['title'][:60]}")

    if enforcement:
        print(f"\n⚖️  ENFORCEMENT ACTIONS ({len(enforcement)}):")
        for item in enforcement[:10]:
            print(f"  {item['title'][:65]}")

    if circulars:
        print(f"\n📋 NEW CIRCULARS/REGULATIONS ({len(circulars)}):")
        for item in circulars[:5]:
            print(f"  {item['title'][:65]}")

    print(f"\n  Total SEBI updates: {len(items)}")
    print(f"{'='*65}")


def main():
    ensure_table()
    watchlist = get_watchlist_symbols()
    items     = fetch_sebi_rss()

    if items:
        save_updates(items)
        print_summary(items, watchlist)
    else:
        logger.warning("No SEBI updates fetched")


if __name__ == "__main__":
    main()
