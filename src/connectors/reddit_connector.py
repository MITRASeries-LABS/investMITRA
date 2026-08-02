"""
investMITRA — Reddit Connector
Posts and comments from r/IndiaInvestments via praw.

OAuth token expires every 24h — praw auto-refreshes.
Captures retail sentiment not reflected in institutional news.

Required env vars:
  REDDIT_CLIENT_ID
  REDDIT_CLIENT_SECRET
  REDDIT_USER_AGENT
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from typing import Iterator

import pandas as pd

from .base import BaseConnector, SourceUnavailableError

logger = logging.getLogger(__name__)

_SUBREDDITS = ["IndiaInvestments", "IndianStockMarket", "Sensex"]
_POST_LIMIT = 100  # per subreddit per run


class RedditConnector(BaseConnector):

    source_id         = "reddit_india_invest"
    domain            = "news_events"
    refresh_frequency = "realtime"
    required_columns  = ["headline", "published_at", "source_id", "url"]
    expected_columns  = []

    def fetch(self, target_date: date) -> pd.DataFrame:
        import praw

        client_id     = os.getenv("REDDIT_CLIENT_ID")
        client_secret = os.getenv("REDDIT_CLIENT_SECRET")
        user_agent    = os.getenv("REDDIT_USER_AGENT", "investMITRA/1.0")

        if not client_id or not client_secret:
            logger.warning("[reddit] Missing credentials — skipping")
            return pd.DataFrame()

        try:
            reddit = praw.Reddit(
                client_id=client_id,
                client_secret=client_secret,
                user_agent=user_agent,
            )
        except Exception as e:
            raise SourceUnavailableError(f"Reddit auth failed: {e}")

        all_rows = []
        for subreddit_name in _SUBREDDITS:
            try:
                rows = self._fetch_subreddit(reddit, subreddit_name)
                all_rows.extend(rows)
                self._polite_sleep(1.0)
            except Exception as e:
                logger.warning("[reddit] Failed r/%s: %s", subreddit_name, e)

        return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()

    def backfill(self, start: date, end: date) -> Iterator[pd.DataFrame]:
        yield self.fetch(start)  # Reddit API has no historical backfill

    def _fetch_subreddit(self, reddit, subreddit_name: str) -> list[dict]:
        subreddit = reddit.subreddit(subreddit_name)
        rows = []

        for post in subreddit.new(limit=_POST_LIMIT):
            try:
                rows.append({
                    "source_id":       self.source_id,
                    "headline":        post.title,
                    "body_snippet":    (post.selftext or "")[:500],
                    "url":             f"https://reddit.com{post.permalink}",
                    "published_at":    datetime.fromtimestamp(
                                           post.created_utc, tz=timezone.utc
                                       ),
                    "entities_isin":   None,
                    "sentiment_score": None,
                    "sentiment_label": None,
                    "event_type":      "social",
                    "upvotes":         post.score,
                    "num_comments":    post.num_comments,
                    "subreddit":       subreddit_name,
                })
            except Exception as e:
                logger.debug("[reddit] Post parse error: %s", e)

        logger.info("[reddit] r/%s — %d posts", subreddit_name, len(rows))
        return rows
