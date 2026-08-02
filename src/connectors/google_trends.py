"""
investMITRA — Google Trends Connector
Search interest trends for NSE-listed stocks.

Query format: "{company_name} share price"
Captures retail investor interest — leads price movements by 1-3 days.

Rate limit: pytrends has unofficial rate limiting.
Process large-caps first, then mid/small caps.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Iterator

import pandas as pd

from .base import BaseConnector, SourceUnavailableError

logger = logging.getLogger(__name__)


class GoogleTrendsConnector(BaseConnector):

    source_id         = "google_trends"
    domain            = "news_events"
    refresh_frequency = "daily"
    required_columns  = ["nse_symbol", "observation_date", "interest_score"]
    expected_columns  = []

    _BATCH_SIZE = 5  # pytrends supports up to 5 keywords per request

    def fetch(self, target_date: date) -> pd.DataFrame:
        try:
            from pytrends.request import TrendReq
        except ImportError:
            logger.warning("[google_trends] pytrends not installed — skipping")
            return pd.DataFrame()

        companies = self._get_large_caps()  # start with large caps
        if not companies:
            return pd.DataFrame()

        pytrends = TrendReq(hl="en-IN", tz=330)  # IST timezone
        all_rows = []

        # Process in batches of 5
        for i in range(0, min(len(companies), 50), self._BATCH_SIZE):
            batch = companies[i:i + self._BATCH_SIZE]
            try:
                rows = self._fetch_batch(pytrends, batch, target_date)
                all_rows.extend(rows)
                self._polite_sleep(3.0)  # Google Trends rate limit
            except Exception as e:
                logger.warning("[google_trends] Batch failed: %s", e)

        return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()

    def backfill(self, start: date, end: date) -> Iterator[pd.DataFrame]:
        current = start
        while current <= end:
            if current.weekday() < 5:
                try:
                    yield self.fetch(current)
                except Exception as e:
                    logger.warning("[google_trends] Skip %s: %s", current, e)
                self._polite_sleep(5.0)
            current += timedelta(days=7)  # weekly batches for backfill

    def _fetch_batch(
        self, pytrends, companies: list[tuple], target_date: date
    ) -> list[dict]:
        keywords = [f"{name} share price" for _, name, _ in companies]
        timeframe = f"{(target_date - timedelta(days=7)).strftime('%Y-%m-%d')} {target_date.strftime('%Y-%m-%d')}"

        pytrends.build_payload(keywords, cat=0, timeframe=timeframe, geo="IN")
        df = pytrends.interest_over_time()

        if df.empty:
            return []

        rows = []
        for _, symbol, company_name in companies:
            keyword = f"{company_name} share price"
            if keyword not in df.columns:
                continue
            for obs_date, interest in df[keyword].items():
                rows.append({
                    "nse_symbol":      symbol,
                    "company_name":    company_name,
                    "observation_date": obs_date.date() if hasattr(obs_date, "date") else obs_date,
                    "interest_score":  int(interest),
                    "keyword":         keyword,
                    "geo":             "IN",
                    "source_id":       self.source_id,
                })

        return rows

    def _get_large_caps(self) -> list[tuple]:
        """Get large cap companies ordered by market cap category."""
        try:
            from src.config.database import get_pg_conn
            with get_pg_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT isin, nse_symbol, company_name
                    FROM investmitra.company_master
                    WHERE is_active = TRUE
                      AND nse_symbol IS NOT NULL
                      AND market_cap_category IN ('LARGE', 'MID')
                    ORDER BY market_cap_category, isin
                    LIMIT 200
                    """
                )
                return cur.fetchall()
        except Exception as e:
            logger.error("[google_trends] DB error: %s", e)
            return []
