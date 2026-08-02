"""
investMITRA — SEBI Circulars Connector
Regulatory circulars and orders from SEBI.

URL: https://www.sebi.gov.in/sebiweb/other/OtherAction.do?doListingAll=yes&type=1
RSS: https://www.sebi.gov.in/sebi_data/attachdocs/aug-2007/1188203015257.xml

Circulars are PDF — extracted via pdfplumber.
LLM classification (category, entities) deferred to Phase 2.
Phase 1: store raw text + metadata.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone, timedelta
from typing import Iterator

import feedparser
import pandas as pd

from .base import BaseConnector, SourceUnavailableError

logger = logging.getLogger(__name__)

_SEBI_RSS = "https://www.sebi.gov.in/sebi_data/attachdocs/aug-2007/1188203015257.xml"
_SEBI_CIRCULARS_PAGE = "https://www.sebi.gov.in/sebiweb/other/OtherAction.do?doListingAll=yes&type=1"


class SEBICircularsConnector(BaseConnector):

    source_id         = "sebi_circulars"
    domain            = "regulatory"
    refresh_frequency = "event"
    required_columns  = ["headline", "published_at", "source_id", "url"]
    expected_columns  = []

    def fetch(self, target_date: date) -> pd.DataFrame:
        rows = []

        # Try RSS first
        try:
            rows.extend(self._fetch_rss())
        except Exception as e:
            logger.warning("[sebi_circulars] RSS failed: %s", e)

        # Fallback: scrape listing page
        if not rows:
            try:
                rows.extend(self._fetch_listing())
            except Exception as e:
                logger.warning("[sebi_circulars] Listing scrape failed: %s", e)

        return pd.DataFrame(rows) if rows else pd.DataFrame()

    def backfill(self, start: date, end: date) -> Iterator[pd.DataFrame]:
        yield self.fetch(start)

    def _fetch_rss(self) -> list[dict]:
        feed = feedparser.parse(_SEBI_RSS)
        rows = []
        for entry in feed.entries:
            try:
                rows.append({
                    "source_id":    self.source_id,
                    "headline":     entry.get("title", "").strip(),
                    "body_snippet": entry.get("summary", "")[:500],
                    "url":          entry.get("link", ""),
                    "published_at": self._parse_date(entry),
                    "event_type":   "regulatory",
                    "doc_type":     "circular",
                })
            except Exception:
                continue
        logger.info("[sebi_circulars] RSS — %d circulars", len(rows))
        return rows

    def _fetch_listing(self) -> list[dict]:
        """Scrape SEBI circulars listing page."""
        from bs4 import BeautifulSoup
        resp = self._get(_SEBI_CIRCULARS_PAGE)
        soup = BeautifulSoup(resp.text, "lxml")
        rows = []

        for row in soup.select("table tr")[1:]:  # skip header
            cols = row.select("td")
            if len(cols) < 3:
                continue
            try:
                date_str = cols[0].get_text(strip=True)
                title    = cols[1].get_text(strip=True)
                link     = cols[1].find("a", href=True)
                url      = f"https://www.sebi.gov.in{link['href']}" if link else ""

                rows.append({
                    "source_id":    self.source_id,
                    "headline":     title,
                    "body_snippet": "",
                    "url":          url,
                    "published_at": pd.to_datetime(date_str, dayfirst=True),
                    "event_type":   "regulatory",
                    "doc_type":     "circular",
                })
            except Exception:
                continue

        logger.info("[sebi_circulars] Listing — %d circulars", len(rows))
        return rows

    def _parse_date(self, entry) -> datetime:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        return datetime.now(timezone.utc)
