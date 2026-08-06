"""
investMITRA — NSE F&O Bhavcopy Connector
Daily Futures & Options OHLCV + Open Interest from NSE.

URL:
  https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{YYYY}/{MMM}/fo{DD}{MMM}{YYYY}bhav.csv.zip

Key notes:
  - Covers: Index Futures, Index Options, Stock Futures, Stock Options
  - Instrument types: FUTIDX, OPTIDX, FUTSTK, OPTSTK
  - Join to equity_prices via underlying symbol → ISIN mapping
  - Open interest is a key signal for derivative-based sentiment features
  - Settlement price (SETTLE_PR) used for mark-to-market, not CLOSE
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
    "INSTRUMENT": "instrument_type",   # FUTIDX | OPTIDX | FUTSTK | OPTSTK
    "SYMBOL":     "nse_symbol",
    "EXPIRY_DT":  "expiry_date",
    "STRIKE_PR":  "strike_price",
    "OPTION_TYP": "option_type",       # CE | PE | XX (for futures)
    "OPEN":       "open",
    "HIGH":       "high",
    "LOW":        "low",
    "CLOSE":      "close",
    "SETTLE_PR":  "settle_price",
    "CONTRACTS":  "contracts",
    "VAL_INLAKH": "turnover_lakhs",
    "OPEN_INT":   "open_interest",
    "CHG_IN_OI":  "oi_change",
    "TIMESTAMP":  "trade_date_raw",
}

# Instrument types to ingest
_INSTRUMENT_TYPES = {"FUTIDX", "OPTIDX", "FUTSTK", "OPTSTK"}


class NSEFOBhavCopyConnector(BaseConnector):

    source_id         = "nse_fo_bhavcopy"
    domain            = "market_data"
    refresh_frequency = "daily"
    required_columns  = ["nse_symbol", "instrument_type", "expiry_date", "trade_date",
                         "open", "high", "low", "close", "open_interest"]
    expected_columns  = list(_COL_MAP.keys())

    _URL_NEW = (
        "https://nsearchives.nseindia.com/content/fo"
        "/BhavCopy_NSE_FO_0_0_0_{date}_F_0000.csv.zip"
    )
    _URL_OLD = (
        "https://nsearchives.nseindia.com/content/historical/DERIVATIVES"
        "/{year}/{month}/fo{dd}{month}{year}bhav.csv.zip"
    )

    def fetch(self, target_date: date) -> pd.DataFrame:
        urls = [
            self._URL_NEW.format(date=target_date.strftime("%Y%m%d")),
            self._URL_OLD.format(
                year=target_date.strftime("%Y"),
                month=target_date.strftime("%b").upper(),
                dd=target_date.strftime("%d"),
            ),
        ]
        for url in urls:
            try:
                logger.info("[nse_fo_bhavcopy] GET %s", url)
                resp = self._get(url)
                return self._parse(resp.content, target_date)
            except Exception as e:
                if "404" in str(e):
                    continue
                raise
        from src.connectors.base import SourceUnavailableError
        raise SourceUnavailableError(f"[nse_fo_bhavcopy] No data for {target_date}")

    def backfill(self, start: date, end: date) -> Iterator[pd.DataFrame]:
        current = start
        while current <= end:
            if current.weekday() < 5:
                try:
                    yield self.fetch(current)
                except SourceUnavailableError as e:
                    logger.warning("[nse_fo_bhavcopy] Skip %s: %s", current, e)
                self._polite_sleep(1.2)
            current += timedelta(days=1)

    def _parse(self, content: bytes, target_date: date) -> pd.DataFrame:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                csv_name = next(n for n in z.namelist() if n.endswith(".csv"))
                with z.open(csv_name) as f:
                    raw = pd.read_csv(f, dtype=str)
        except Exception as e:
            raise SourceUnavailableError(
                f"[nse_fo_bhavcopy] Parse failed {target_date}: {e}"
            ) from e

        raw.columns = raw.columns.str.strip()
        return self._normalise(raw, target_date)

    def _normalise(self, raw: pd.DataFrame, target_date: date) -> pd.DataFrame:
        df = raw.rename(columns=_COL_MAP)

        # Filter to known instrument types
        if "instrument_type" in df.columns:
            df = df[df["instrument_type"].isin(_INSTRUMENT_TYPES)].copy()

        # Type conversions — price columns
        for col in ["open", "high", "low", "close", "settle_price", "strike_price"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Integer columns
        for col in ["contracts", "open_interest", "oi_change"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

        # Turnover: lakhs → crore
        if "turnover_lakhs" in df.columns:
            df["turnover_cr"] = pd.to_numeric(df["turnover_lakhs"], errors="coerce") / 100.0

        # Expiry date normalise
        if "expiry_date" in df.columns:
            df["expiry_date"] = pd.to_datetime(
                df["expiry_date"], format="%d-%b-%Y", errors="coerce"
            ).dt.date

        # Option type: futures have 'XX' — normalise to None
        if "option_type" in df.columns:
            df["option_type"] = df["option_type"].replace("XX", None)

        df["trade_date"] = target_date
        df["source"]     = "NSE"

        keep = [
            "nse_symbol", "instrument_type", "expiry_date", "strike_price",
            "option_type", "trade_date", "source",
            "open", "high", "low", "close", "settle_price",
            "contracts", "turnover_cr", "open_interest", "oi_change",
        ]

        return df[[c for c in keep if c in df.columns]].reset_index(drop=True)
