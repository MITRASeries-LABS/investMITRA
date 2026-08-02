"""
investMITRA — RSS Feeds Connector
Real-time news from Economic Times, Mint, Business Standard.

Feeds polled every 30 min during market hours.
Articles linked to ISINs via NER entity linking pipeline (Phase 2).
FinBERT sentiment scoring runs as a separate step after ingestion.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Iterator

import feedparser
import pandas as pd

from .base import BaseConnector, SourceUnavailableError

logger = logging.getLogger(__name__)

_RSS_FEEDS = {
    "et_rss": {
        "name": "Economic Times",
        "urls": [
            "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
            "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
        ],
    },
    "mint_rss": {
        "name": "Mint",
        "urls": [
            "https://www.livemint.com/rss/markets",
            "https://www.livemint.com/rss/companies",
        ],
    },
    "bs_rss": {
        "name": "Business Standard",
        "urls": [
            "https://www.business-standard.com/rss/markets-106.rss",
            "https://www.business-standard.com/rss/companies-101.rss",
        ],
    },
}


class RSSFeedsConnector(BaseConnector):

    source_id         = "et_rss"   # default — overridden per feed
    domain            = "news_events"
    refresh_frequency = "realtime"
    required_columns  = ["headline", "published_at", "source_id", "url"]
    expected_columns  = []

    def fetch(self, target_date: date) -> pd.DataFrame:
        all_rows = []
        for source_id, config in _RSS_FEEDS.items():
            for url in config["urls"]:
                try:
                    rows = self._fetch_feed(url, source_id)
                    all_rows.extend(rows)
                    self._polite_sleep(0.5)
                except Exception as e:
                    logger.warning("[rss] Failed %s %s: %s", source_id, url, e)

        return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()

    def backfill(self, start: date, end: date) -> Iterator[pd.DataFrame]:
        yield self.fetch(start)  # RSS has no historical backfill

    def _fetch_feed(self, url: str, source_id: str) -> list[dict]:
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            raise SourceUnavailableError(f"RSS parse failed {url}: {e}")

        rows = []
        for entry in feed.entries:
            try:
                published_at = self._parse_date(entry)
                rows.append({
                    "source_id":    source_id,
                    "headline":     entry.get("title", "").strip(),
                    "body_snippet": entry.get("summary", "")[:500],
                    "url":          entry.get("link", ""),
                    "published_at": published_at,
                    "entities_isin":    None,  # filled by NER pipeline
                    "sentiment_score":  None,  # filled by FinBERT
                    "sentiment_label":  None,
                    "event_type":       "general",
                })
            except Exception as e:
                logger.debug("[rss] Entry parse error: %s", e)

        logger.info("[rss] %s — %d articles from %s", source_id, len(rows), url)
        return rows

    def _parse_date(self, entry) -> datetime:
        """Parse published date from RSS entry."""
        import time
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        if hasattr(entry, "updated_parsed") and entry.updated_parsed:
            return datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
        return datetime.now(timezone.utc)
