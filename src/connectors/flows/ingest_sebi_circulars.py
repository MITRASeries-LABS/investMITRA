"""
investMITRA — Regulatory Flows
Runs via GitHub Actions (regulatory.yml)
"""

from __future__ import annotations
import logging
from datetime import date, datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_RBI_POLICY_RSS = "https://rbi.org.in/Scripts/RSSFeedsRoot.aspx?Id=96"
_PIB_RSS        = "https://pib.gov.in/RssMain.aspx?ModID=6&Lang=1&Regid=3"


def _write_regulatory_to_db(df, source_id: str):
    from src.config.database import get_pg_conn
    with get_pg_conn() as conn:
        cur = conn.cursor()
        written = 0
        for _, row in df.iterrows():
            try:
                cur.execute(
                    """
                    INSERT INTO investmitra.news_events
                        (published_at, source_id, headline, body_snippet,
                         url, event_type)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        row.get("published_at"),
                        source_id,
                        row.get("headline"),
                        row.get("body_snippet", ""),
                        row.get("url", ""),
                        "regulatory",
                    ),
                )
                written += 1
            except Exception as e:
                logger.debug("DB write failed: %s", e)
        cur.close()
        logger.info("Wrote %d regulatory rows", written)


def ingest_sebi_circulars():
    from src.connectors.sebi_circulars import SEBICircularsConnector
    from src.transforms.lake_writer import write_to_lake
    from src.quality.db_logger import log_pipeline_run

    logger.info("SEBI Circulars ingestion started")
    connector = SEBICircularsConnector()
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    target_date = date.today()

    df, result = connector.ingest(target_date)
    if df.empty:
        logger.warning("No SEBI circulars")
        return

    write_to_lake(df=df, domain="regulatory", table="sebi_circulars",
                  partition_date=target_date, source_id="sebi_circulars",
                  quality_score=result.quality_score, run_id=run_id)
    _write_regulatory_to_db(df, "sebi_circulars")
    log_pipeline_run(source_id="sebi_circulars", run_date=target_date,
                     status="success", rows_ingested=len(df),
                     quality_score=result.quality_score)
    logger.info("SEBI Circulars done — rows=%d", len(df))


def ingest_rbi_policy():
    import feedparser
    from src.transforms.lake_writer import write_to_lake
    from src.quality.db_logger import log_pipeline_run
    import pandas as pd
    from datetime import timezone

    logger.info("RBI Policy ingestion started")
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    target_date = date.today()

    feed = feedparser.parse(_RBI_POLICY_RSS)
    rows = []
    for entry in feed.entries:
        try:
            rows.append({
                "source_id":    "rbi_policy",
                "headline":     entry.get("title", "").strip(),
                "body_snippet": entry.get("summary", "")[:500],
                "url":          entry.get("link", ""),
                "published_at": datetime.now(timezone.utc),
                "event_type":   "regulatory",
            })
        except Exception:
            continue

    if not rows:
        logger.warning("No RBI policy data")
        return

    df = pd.DataFrame(rows)
    write_to_lake(df=df, domain="regulatory", table="rbi_policy",
                  partition_date=target_date, source_id="rbi_policy",
                  quality_score=80, run_id=run_id)
    _write_regulatory_to_db(df, "rbi_policy")
    log_pipeline_run(source_id="rbi_policy", run_date=target_date,
                     status="success", rows_ingested=len(df), quality_score=80)
    logger.info("RBI Policy done — rows=%d", len(df))


def ingest_pib_press():
    import feedparser
    from src.transforms.lake_writer import write_to_lake
    from src.quality.db_logger import log_pipeline_run
    import pandas as pd
    from datetime import timezone

    logger.info("PIB Press Releases ingestion started")
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    target_date = date.today()

    feed = feedparser.parse(_PIB_RSS)
    rows = []
    for entry in feed.entries:
        try:
            rows.append({
                "source_id":    "pib_press",
                "headline":     entry.get("title", "").strip(),
                "body_snippet": entry.get("summary", "")[:500],
                "url":          entry.get("link", ""),
                "published_at": datetime.now(timezone.utc),
                "event_type":   "regulatory",
            })
        except Exception:
            continue

    if not rows:
        logger.warning("No PIB press data")
        return

    df = pd.DataFrame(rows)
    write_to_lake(df=df, domain="regulatory", table="pib_press",
                  partition_date=target_date, source_id="pib_press",
                  quality_score=80, run_id=run_id)
    _write_regulatory_to_db(df, "pib_press")
    log_pipeline_run(source_id="pib_press", run_date=target_date,
                     status="success", rows_ingested=len(df), quality_score=80)
    logger.info("PIB Press done — rows=%d", len(df))


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    if target in ("all", "sebi"):  ingest_sebi_circulars()
    if target in ("all", "rbi"):   ingest_rbi_policy()
    if target in ("all", "pib"):   ingest_pib_press()
