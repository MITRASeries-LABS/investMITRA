"""
investMITRA — BSE EOD Connector
Daily EOD equity prices from BSE.

URL:
  https://www.bseindia.com/download/BhavCopy/Equity/EQ{DDMMYYYY}_CSV.ZIP

Key differences from NSE:
  - Uses 6-digit BSE code, not symbol — must map to ISIN via company_master
  - No delivery % data (NSE only)
  - Turnover already in lakhs — convert to crore
  - Cross-validate close price against NSE — discrepancy > 2% → quarantine flag
"""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import date, timedelta
from typing import Iterator

import pandas as pd

from .base import BaseConnector, SourceUnavailableError

logger = logging.getLogger(__name__)

_COL_MAP = {
    "SCRIP_CD":   "bse_code",
    "SC_NAME":    "company_name",
    "SC_GROUP":   "group",
    "SC_TYPE":    "sc_type",
    "OPEN":       "open",
    "HIGH":       "high",
    "LOW":        "low",
    "CLOSE":      "close",
    "LAST":       "last_price",
    "PREVCLOSE":  "prev_close",
    "NO_TRADES":  "total_trades",
    "NO_OF_SHRS": "volume",
    "NET_TURNOV": "turnover_lakhs",
    "ISIN_CODE":  "isin",
    "TDCLOINDI":  "td_close_indicator",
}

# BSE equity groups to retain
_EQUITY_GROUPS = {"A", "B", "E", "F", "S", "T", "XT", "Z", "X"}


class BSEEODConnector(BaseConnector):

    source_id         = "bse_eod"
    domain            = "market_data"
    refresh_frequency = "daily"
    required_columns  = ["isin", "trade_date", "open", "high", "low", "close", "volume"]
    expected_columns  = list(_COL_MAP.keys())

    _URL = "https://www.bseindia.com/download/BhavCopy/Equity/EQ{date}_CSV.ZIP"

    def fetch(self, target_date: date) -> pd.DataFrame:
        url = self._URL.format(date=target_date.strftime("%d%m%Y"))
        logger.info("[bse_eod] GET %s", url)

        # BSE requires different Referer header
        resp = self._get(url, headers={"Referer": "https://www.bseindia.com"})
        return self._parse(resp.content, target_date)

    def backfill(self, start: date, end: date) -> Iterator[pd.DataFrame]:
        current = start
        while current <= end:
            if current.weekday() < 5:
                try:
                    yield self.fetch(current)
                except SourceUnavailableError as e:
                    logger.warning("[bse_eod] Skip %s: %s", current, e)
                self._polite_sleep(1.5)  # BSE is stricter on rate limits
            current += timedelta(days=1)

    def _parse(self, content: bytes, target_date: date) -> pd.DataFrame:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                csv_name = next(n for n in z.namelist() if n.endswith(".CSV") or n.endswith(".csv"))
                with z.open(csv_name) as f:
                    raw = pd.read_csv(f, dtype=str)
        except Exception as e:
            raise SourceUnavailableError(f"[bse_eod] Parse failed {target_date}: {e}") from e

        raw.columns = raw.columns.str.strip()
        return self._normalise(raw, target_date)

    def _normalise(self, raw: pd.DataFrame, target_date: date) -> pd.DataFrame:
        df = raw.rename(columns=_COL_MAP)

        # Filter to equity groups only
        if "group" in df.columns:
            df = df[df["group"].isin(_EQUITY_GROUPS)].copy()

        # Type conversions
        for col in ["open", "high", "low", "close", "last_price", "prev_close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("Int64")

        if "turnover_lakhs" in df.columns:
            # BSE turnover in lakhs — convert to crore
            df["turnover_cr"] = pd.to_numeric(df["turnover_lakhs"], errors="coerce") / 100.0

        # BSE code as string with leading zeros
        if "bse_code" in df.columns:
            df["bse_code"] = df["bse_code"].astype(str).str.zfill(6)

        df["trade_date"] = target_date
        df["source"]     = "BSE"
        df["vwap"]       = None  # BSE does not provide VWAP

        keep = ["isin", "bse_code", "company_name", "group", "trade_date", "source",
                "open", "high", "low", "close", "vwap",
                "volume", "turnover_cr", "total_trades", "prev_close"]

        return df[[c for c in keep if c in df.columns]].reset_index(drop=True)
