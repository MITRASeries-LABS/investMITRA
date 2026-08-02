"""
investMITRA — MOSPI Connector
Macroeconomic data from Ministry of Statistics & Programme Implementation.

Sources:
  - GDP quarterly estimates (advance + revised)
  - IIP monthly (industrial production)
  - CPI monthly (consumer price index)
  - WPI monthly (wholesale price index)

Data downloaded as XLS/XLSX from MOSPI website.
Critical: store data_vintage for revision tracking — GDP estimates are revised 3 times.

URL base: https://mospi.gov.in/
"""

from __future__ import annotations

import io
import logging
from datetime import date, timedelta
from typing import Iterator

import pandas as pd
import requests

from .base import BaseConnector, SourceUnavailableError

logger = logging.getLogger(__name__)

_MOSPI_SOURCES = {
    "IN_GDP_QOQ": {
        "url": "https://mospi.gov.in/sites/default/files/GDP_Q.xlsx",
        "sheet": 0,
        "unit": "%",
        "freq": "quarterly",
    },
    "IN_IIP_YOY": {
        "url": "https://mospi.gov.in/sites/default/files/iip.xlsx",
        "sheet": 0,
        "unit": "%",
        "freq": "monthly",
    },
    "IN_CPI_YOY": {
        "url": "https://mospi.gov.in/sites/default/files/cpi.xlsx",
        "sheet": 0,
        "unit": "%",
        "freq": "monthly",
    },
    "IN_WPI_YOY": {
        "url": "https://eaindustry.nic.in/download_data_0405.asp",
        "sheet": 0,
        "unit": "%",
        "freq": "monthly",
    },
}


class MOSPIConnector(BaseConnector):

    source_id         = "mospi"
    domain            = "macroeconomic"
    refresh_frequency = "monthly"
    required_columns  = ["indicator_id", "observation_date", "value"]
    expected_columns  = []

    def fetch(self, target_date: date) -> pd.DataFrame:
        all_rows = []
        for indicator_id, config in _MOSPI_SOURCES.items():
            try:
                rows = self._fetch_indicator(indicator_id, config)
                all_rows.extend(rows)
                self._polite_sleep(2.0)
            except Exception as e:
                logger.warning("[mospi] Failed %s: %s", indicator_id, e)

        return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()

    def backfill(self, start: date, end: date) -> Iterator[pd.DataFrame]:
        yield self.fetch(start)

    def _fetch_indicator(self, indicator_id: str, config: dict) -> list[dict]:
        try:
            resp = self._session.get(config["url"], timeout=30, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://mospi.gov.in",
            })
            resp.raise_for_status()
            df = pd.read_excel(io.BytesIO(resp.content), sheet_name=config["sheet"])
        except Exception as e:
            raise SourceUnavailableError(f"MOSPI failed {indicator_id}: {e}")

        rows = []
        # MOSPI Excel layouts vary — try to find date and value columns
        date_col  = self._find_date_col(df)
        value_col = self._find_value_col(df)

        if date_col is None or value_col is None:
            logger.warning("[mospi] Could not parse columns for %s", indicator_id)
            return []

        for _, row in df.iterrows():
            try:
                obs_date = pd.to_datetime(row[date_col], dayfirst=True).date()
                value    = float(row[value_col])
                rows.append({
                    "indicator_id":     indicator_id,
                    "source_id":        self.source_id,
                    "observation_date": obs_date,
                    "value":            value,
                    "unit":             config["unit"],
                    "data_vintage":     date.today().isoformat(),
                    "is_revised":       False,
                })
            except Exception:
                continue

        logger.info("[mospi] %s — %d observations", indicator_id, len(rows))
        return rows

    def _find_date_col(self, df: pd.DataFrame):
        for col in df.columns:
            sample = df[col].dropna().head(5)
            try:
                pd.to_datetime(sample, dayfirst=True)
                return col
            except Exception:
                continue
        return None

    def _find_value_col(self, df: pd.DataFrame):
        numeric_cols = df.select_dtypes(include="number").columns.tolist()
        return numeric_cols[0] if numeric_cols else None
