"""
investMITRA — News & Sentiment Flows
Runs via GitHub Actions (news_sentiment.yml)
"""

from __future__ import annotations
import logging
import os
from datetime import date, datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _write_news_to_db(df):
    """Write news articles to Neon news_events table."""
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
                        row.get("source_id"),
                        row.get("headline"),
                        row.get("body_snippet"),
                        row.get("url"),
                        row.get("event_type", "general"),
                    ),
                )
                written += 1
            except Exception as e:
                logger.debug("DB write failed: %s", e)
        cur.close()
        logger.info("Wrote %d news rows to DB", written)


def ingest_rss_feeds():
    from src.connectors.rss_feeds import RSSFeedsConnector
    from src.transforms.lake_writer import write_to_lake
    from src.quality.db_logger import log_pipeline_run

    logger.info("RSS Feeds ingestion started")
    connector = RSSFeedsConnector()
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    target_date = date.today()

    df, result = connector.ingest(target_date)
    if df.empty:
        logger.warning("No RSS data")
        return

    write_to_lake(df=df, domain="news_events", table="rss_articles",
                  partition_date=target_date, source_id="et_rss",
                  quality_score=result.quality_score, run_id=run_id)
    _write_news_to_db(df)
    log_pipeline_run(source_id="et_rss", run_date=target_date,
                     status="success", rows_ingested=len(df),
                     quality_score=result.quality_score)
    logger.info("RSS done — rows=%d", len(df))


def ingest_reddit():
    from src.connectors.reddit_connector import RedditConnector
    from src.transforms.lake_writer import write_to_lake
    from src.quality.db_logger import log_pipeline_run

    logger.info("Reddit ingestion started")
    connector = RedditConnector()
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    target_date = date.today()

    df, result = connector.ingest(target_date)
    if df.empty:
        logger.warning("No Reddit data — check credentials")
        return

    write_to_lake(df=df, domain="news_events", table="reddit_posts",
                  partition_date=target_date, source_id="reddit_india_invest",
                  quality_score=result.quality_score, run_id=run_id)
    _write_news_to_db(df)
    log_pipeline_run(source_id="reddit_india_invest", run_date=target_date,
                     status="success", rows_ingested=len(df),
                     quality_score=result.quality_score)
    logger.info("Reddit done — rows=%d", len(df))


def ingest_google_trends():
    from src.connectors.google_trends import GoogleTrendsConnector
    from src.transforms.lake_writer import write_to_lake
    from src.quality.db_logger import log_pipeline_run

    logger.info("Google Trends ingestion started")
    connector = GoogleTrendsConnector()
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    target_date = date.today()

    df, result = connector.ingest(target_date)
    if df.empty:
        logger.warning("No Google Trends data")
        return

    write_to_lake(df=df, domain="news_events", table="google_trends",
                  partition_date=target_date, source_id="google_trends",
                  quality_score=result.quality_score, run_id=run_id)
    log_pipeline_run(source_id="google_trends", run_date=target_date,
                     status="success", rows_ingested=len(df),
                     quality_score=result.quality_score)
    logger.info("Google Trends done — rows=%d", len(df))


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    if target in ("all", "rss"):     ingest_rss_feeds()
    if target in ("all", "reddit"):  ingest_reddit()
    if target in ("all", "trends"):  ingest_google_trends()
