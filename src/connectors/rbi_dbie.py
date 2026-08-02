"""
investMITRA — RBI DBIE Connector
Macroeconomic indicators from RBI Database of Indian Economy.

Free API key required: https://dbie.rbi.org.in/DBIE/dbie.rbi?site=home
Key indicators:
  - Repo rate (policy rate)
  - CPI inflation (headline + core)
  - FX reserves (USD billion)
  - M3 money supply
  - USD/INR exchange rate

API endpoint:
  https://dbie.rbi.org.in/DBIE/dbie.rbi?site=api&type=json&seriesid={series_id}
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Iterator

import pandas as pd

from .base import BaseConnector, SourceUnavailableError

logger = logging.getLogger(__name__)

# RBI DBIE Series IDs for key indicators
_SERIES = {
    "IN_REPO_RATE":   "DBIRPOLICYRR",   # Repo rate
    "IN_CPI_YOY":     "DBICPICOMBY",    # CPI headline YoY
    "IN_FX_RESERVES": "DBIRFOREXRES",   # FX reserves USD bn
    "IN_M3_YOY":      "DBIM3WY",        # M3 money supply YoY
    "IN_USD_INR":     "DBIEXRUSD",      # USD/INR exchange rate
    "IN_IIP_YOY":     "DBIIIPGENBY",    # IIP (industrial output) YoY
}

_RBI_API = "https://dbie.rbi.org.in/DBIE/dbie.rbi?site=api&type=json&seriesid={series_id}"


class RBIDBIEConnector(BaseConnector):

    source_id         = "rbi_dbie"
    domain            = "macroeconomic"
    refresh_frequency = "weekly"
    required_columns  = ["indicator_id", "observation_date", "value"]
    expected_columns  = []

    def fetch(self, target_date: date) -> pd.DataFrame:
        all_rows = []
        for indicator_id, series_id in _SERIES.items():
            try:
                rows = self._fetch_series(indicator_id, series_id)
                all_rows.extend(rows)
                self._polite_sleep(1.0)
            except Exception as e:
                logger.warning("[rbi_dbie] Failed %s: %s", indicator_id, e)

        return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()

    def backfill(self, start: date, end: date) -> Iterator[pd.DataFrame]:
        yield self.fetch(start)  # RBI API returns full history

    def _fetch_series(self, indicator_id: str, series_id: str) -> list[dict]:
        import os
        api_key = os.getenv("RBI_DBIE_API_KEY", "")
        url = _RBI_API.format(series_id=series_id)
        if api_key:
            url += f"&apikey={api_key}"

        try:
            resp = self._get(url)
            data = resp.json()
        except Exception as e:
            raise SourceUnavailableError(f"RBI DBIE failed {indicator_id}: {e}")

        rows = []
        for item in data.get("data", []) or data.get("observations", []):
            try:
                obs_date = pd.to_datetime(
                    item.get("TIME_PERIOD") or item.get("date"), dayfirst=True
                ).date()
                value = float(item.get("OBS_VALUE") or item.get("value"))
                rows.append({
                    "indicator_id":     indicator_id,
                    "source_id":        self.source_id,
                    "observation_date": obs_date,
                    "value":            value,
                    "unit":             self._get_unit(indicator_id),
                    "data_vintage":     date.today().isoformat(),
                    "is_revised":       False,
                })
            except Exception:
                continue

        logger.info("[rbi_dbie] %s — %d observations", indicator_id, len(rows))
        return rows

    def _get_unit(self, indicator_id: str) -> str:
        units = {
            "IN_REPO_RATE":   "%",
            "IN_CPI_YOY":     "%",
            "IN_FX_RESERVES": "USD_BN",
            "IN_M3_YOY":      "%",
            "IN_USD_INR":     "INR",
            "IN_IIP_YOY":     "%",
        }
        return units.get(indicator_id, "")
