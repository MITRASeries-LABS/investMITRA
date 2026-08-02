"""
investMITRA — NSE Circuit Limits Connector
Daily upper and lower circuit limit prices from NSE.

URL:
  https://nsearchives.nseindia.com/archives/equities/mto/MTO_{DDMMYYYY}.DAT

Notes:
  - Circuit limits are price bands set by NSE/SEBI
  - Stocks hitting upper/lower circuit = strong momentum signal
  - Stored in equity_prices.circuit_upper and circuit_lower columns
  - Also useful for data quality: price outside circuit = bad data
"""

from __future__ import annotations

import io
import logging
from datetime import date, timedelta
from typing import Iterator

import pandas as pd

from .base import BaseConnector, SourceUnavailableError

logger = logging.getLogger(__name__)

_COL_MAP = {
    "Symbol":        "nse_symbol",
    "Series":        "series",
    "High Price":    "circuit_upper",
    "Low Price":     "circuit_lower",
    "Prev Close":    "prev_close",
}

_URL = "https://nsearchives.nseindia.com/archives/equities/mto/MTO_{date}.DAT"


class NSECircuitLimitsConnector(BaseConnector):

    source_id         = "nse_circuit_limits"
    domain            = "market_data"
    refresh_frequency = "daily"
    required_columns  = ["nse_symbol", "trade_date", "circuit_upper", "circuit_lower"]
    expected_columns  = list(_COL_MAP.keys())

    def fetch(self, target_date: date) -> pd.DataFrame:
        url = _URL.format(date=target_date.strftime("%d%m%Y"))
        logger.info("[nse_circuit_limits] GET %s", url)
        resp = self._get(url)
        return self._parse(resp.text, target_date)

    def backfill(self, start: date, end: date) -> Iterator[pd.DataFrame]:
        current = start
        while current <= end:
            if current.weekday() < 5:
                try:
                    yield self.fetch(current)
                except SourceUnavailableError as e:
                    logger.warning("[nse_circuit_limits] Skip %s: %s", current, e)
                self._polite_sleep(1.2)
            current += timedelta(days=1)

    def _parse(self, text: str, target_date: date) -> pd.DataFrame:
        try:
            # Skip header rows — circuit limits DAT has metadata on first few lines
            lines = text.strip().split("\n")
            # Find the header line
            header_idx = next(
                i for i, line in enumerate(lines)
                if "Symbol" in line or "SYMBOL" in line
            )
            csv_text = "\n".join(lines[header_idx:])
            raw = pd.read_csv(io.StringIO(csv_text), dtype=str)
        except Exception as e:
            raise SourceUnavailableError(
                f"[nse_circuit_limits] Parse failed {target_date}: {e}"
            ) from e

        raw.columns = raw.columns.str.strip()
        return self._normalise(raw, target_date)

    def _normalise(self, raw: pd.DataFrame, target_date: date) -> pd.DataFrame:
        df = raw.rename(columns={k: v for k, v in _COL_MAP.items() if k in raw.columns})

        # Filter EQ series
        if "series" in df.columns:
            df = df[df["series"].str.strip() == "EQ"].copy()

        for col in ["circuit_upper", "circuit_lower", "prev_close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df["trade_date"] = target_date
        df["source"]     = "NSE"

        keep = ["nse_symbol", "series", "trade_date", "source",
                "circuit_upper", "circuit_lower", "prev_close"]

        return df[[c for c in keep if c in df.columns]].reset_index(drop=True)
