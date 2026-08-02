"""
ClearedCircle — Base Connector Interface
All data source connectors inherit from BaseConnector.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Iterator, Optional

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class SourceUnavailableError(Exception):
    """Raised when a source cannot be reached after all retries."""


class SchemaValidationError(Exception):
    """Raised when ingested data fails schema checks."""


@dataclass
class ValidationResult:
    is_valid: bool
    quality_score: int
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rows_total: int = 0

    def deduct(self, points: int, reason: str) -> None:
        self.quality_score = max(0, self.quality_score - points)
        self.issues.append(reason)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


class BaseConnector(ABC):
    """
    Standard interface all ClearedCircle connectors implement.

    Subclasses must define:
        source_id           str
        domain              str
        refresh_frequency   str
        required_columns    list[str]
        expected_columns    list[str]   — for schema drift detection
    """

    source_id: str
    domain: str
    refresh_frequency: str
    required_columns: list[str] = []
    expected_columns: list[str] = []

    def __init__(self):
        self._session = self._build_session()

    # ------------------------------------------------------------------
    # Must implement
    # ------------------------------------------------------------------

    @abstractmethod
    def fetch(self, target_date: date) -> pd.DataFrame:
        """Fetch raw data for date. Raises SourceUnavailableError on failure."""

    @abstractmethod
    def backfill(self, start: date, end: date) -> Iterator[pd.DataFrame]:
        """Yield DataFrames for each date in [start, end]."""

    # ------------------------------------------------------------------
    # Shared pipeline
    # ------------------------------------------------------------------

    def ingest(self, target_date: date) -> tuple[pd.DataFrame, ValidationResult]:
        """fetch → tag → validate. Returns (df, result)."""
        logger.info("[%s] Ingesting %s", self.source_id, target_date)
        df = self.fetch(target_date)
        df = self._tag_metadata(df)
        result = self.validate_schema(df)

        for w in result.warnings:
            logger.warning("[%s] %s", self.source_id, w)

        if result.is_valid:
            logger.info("[%s] OK — score=%d rows=%d", self.source_id, result.quality_score, len(df))
        else:
            logger.error("[%s] FAILED — score=%d issues=%s", self.source_id, result.quality_score, result.issues)

        return df, result

    def validate_schema(self, df: pd.DataFrame) -> ValidationResult:
        result = ValidationResult(is_valid=True, quality_score=100, rows_total=len(df))

        if df.empty:
            result.deduct(100, "Empty DataFrame")
            result.is_valid = False
            return result

        # Required columns + null check
        for col in self.required_columns:
            if col not in df.columns:
                result.deduct(20, f"Missing required column: {col}")
            elif df[col].isna().mean() > 0.05:
                result.deduct(20, f">5% nulls in: {col}")

        # Schema drift
        if self.expected_columns:
            missing = set(self.expected_columns) - set(df.columns)
            new_cols = set(df.columns) - set(self.expected_columns)
            if missing:
                result.deduct(15, f"Schema drift — missing: {missing}")
                self._alert_slack(f"⚠️ [{self.source_id}] Schema drift: missing {missing}")
            if new_cols:
                result.warn(f"New columns (non-breaking): {new_cols}")

        # Low row count
        if len(df) < 10:
            result.deduct(20, f"Suspiciously low row count: {len(df)}")

        # Duplicate check
        pk = [c for c in self.required_columns if c in df.columns]
        if pk and df.duplicated(subset=pk).mean() > 0.01:
            result.deduct(10, "Deduplication removed >1% of rows")

        result.is_valid = result.quality_score >= 50
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _tag_metadata(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["source_id"]   = self.source_id
        df["ingested_at"] = datetime.now(timezone.utc)
        return df

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=5, backoff_factor=2,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.mount("http://",  HTTPAdapter(max_retries=retry))
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept":     "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer":    "https://www.nseindia.com",
        })
        return session

    def _get(self, url: str, **kwargs) -> requests.Response:
        try:
            resp = self._session.get(url, timeout=30, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as e:
            raise SourceUnavailableError(f"[{self.source_id}] {url}: {e}") from e

    def _polite_sleep(self, seconds: float = 1.2) -> None:
        time.sleep(seconds)

    def _alert_slack(self, message: str) -> None:
        import os
        webhook = os.getenv("SLACK_WEBHOOK_URL")
        if webhook:
            try:
                requests.post(webhook, json={"text": message}, timeout=5)
            except Exception:
                pass
        else:
            logger.warning("SLACK: %s", message)
