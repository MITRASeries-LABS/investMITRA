"""
investMITRA — FRED API Connector
Global macroeconomic indicators from Federal Reserve Economic Data.

Free API key: https://fred.stlouisfed.org/docs/api/api_key.html
Key indicators for Indian equity context:
  - USD/INR exchange rate
  - WTI crude oil price
  - Gold price (USD/oz)
  - US 10-year Treasury yield
  - Fed Funds rate
  - US CPI YoY
  - DXY (US Dollar Index)
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Iterator

import pandas as pd

from .base import BaseConnector, SourceUnavailableError

logger = logging.getLogger(__name__)

_FRED_SERIES = {
    "USD_INR":       "DEXINUS",    # USD/INR daily
    "WTI_OIL":       "DCOILWTICO", # WTI crude oil (USD/barrel)
    "GOLD_USD":      "GOLDAMGBD228NLBM",  # Gold price (USD/troy oz)
    "US_10Y_YIELD":  "DGS10",      # US 10-year Treasury yield
    "FED_FUNDS":     "FEDFUNDS",   # Federal funds rate
    "US_CPI_YOY":    "CPIAUCSL",   # US CPI (compute YoY in transform)
    "DXY":           "DTWEXBGS",   # US Dollar Index
}

_FRED_API = "https://api.stlouisfed.org/fred/series/observations"


class FREDConnector(BaseConnector):

    source_id         = "fred_api"
    domain            = "macroeconomic"
    refresh_frequency = "daily"
    required_columns  = ["indicator_id", "observation_date", "value"]
    expected_columns  = []

    def fetch(self, target_date: date) -> pd.DataFrame:
        api_key = os.getenv("FRED_API_KEY", "")
        if not api_key:
            logger.warning("[fred] No FRED_API_KEY set — skipping")
            return pd.DataFrame()

        from_date = (target_date - timedelta(days=7)).strftime("%Y-%m-%d")
        to_date   = target_date.strftime("%Y-%m-%d")

        all_rows = []
        for indicator_id, series_id in _FRED_SERIES.items():
            try:
                rows = self._fetch_series(
                    indicator_id, series_id, api_key, from_date, to_date
                )
                all_rows.extend(rows)
                self._polite_sleep(0.5)
            except Exception as e:
                logger.warning("[fred] Failed %s: %s", indicator_id, e)

        return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()

    def backfill(self, start: date, end: date) -> Iterator[pd.DataFrame]:
        """Backfill fetches full history in one call per series."""
        api_key = os.getenv("FRED_API_KEY", "")
        if not api_key:
            return

        all_rows = []
        for indicator_id, series_id in _FRED_SERIES.items():
            try:
                rows = self._fetch_series(
                    indicator_id, series_id, api_key,
                    start.strftime("%Y-%m-%d"),
                    end.strftime("%Y-%m-%d"),
                )
                all_rows.extend(rows)
                self._polite_sleep(0.5)
            except Exception as e:
                logger.warning("[fred] Backfill failed %s: %s", indicator_id, e)

        if all_rows:
            yield pd.DataFrame(all_rows)

    def _fetch_series(
        self, indicator_id: str, series_id: str,
        api_key: str, from_date: str, to_date: str
    ) -> list[dict]:
        params = {
            "series_id":         series_id,
            "api_key":           api_key,
            "file_type":         "json",
            "observation_start": from_date,
            "observation_end":   to_date,
        }
        try:
            resp = self._session.get(_FRED_API, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            raise SourceUnavailableError(f"FRED failed {series_id}: {e}")

        rows = []
        for obs in data.get("observations", []):
            try:
                if obs.get("value") == ".":  # FRED uses "." for missing
                    continue
                rows.append({
                    "indicator_id":     indicator_id,
                    "source_id":        self.source_id,
                    "observation_date": date.fromisoformat(obs["date"]),
                    "value":            float(obs["value"]),
                    "unit":             self._get_unit(indicator_id),
                    "data_vintage":     date.today().isoformat(),
                    "is_revised":       obs.get("realtime_start") != obs.get("realtime_end"),
                })
            except Exception:
                continue

        logger.info("[fred] %s (%s) — %d observations", indicator_id, series_id, len(rows))
        return rows

    def _get_unit(self, indicator_id: str) -> str:
        units = {
            "USD_INR":      "INR",
            "WTI_OIL":      "USD/BBL",
            "GOLD_USD":     "USD/OZ",
            "US_10Y_YIELD": "%",
            "FED_FUNDS":    "%",
            "US_CPI_YOY":   "INDEX",
            "DXY":          "INDEX",
        }
        return units.get(indicator_id, "")
