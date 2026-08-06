"""
ClearedCircle — NSE Bhavcopy Connector
Daily EOD equity prices from NSE archives.

URL:
  https://nsearchives.nseindia.com/content/historical/EQUITIES/{YYYY}/{MMM}/cm{DD}{MMM}{YYYY}bhav.csv.zip

Handles both pre/post-2019 format changes automatically.
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

_EQUITY_SERIES = {"EQ", "BE", "BZ", "BL", "GC", "IL", "ST", "GB", "SM", "MF"}

_COL_MAP = {
    "ISIN":            "isin",
    "TckrSymb":        "nse_symbol",
    "SctySrs":         "series",
    "FinInstrmNm":     "company_name",
    "OpnPric":         "open",
    "HghPric":         "high",
    "LwPric":          "low",
    "ClsPric":         "close",
    "LastPric":        "last_price",
    "PrvsClsgPric":    "prev_close",
    "TtlTradgVol":     "volume",
    "TtlTrfVal":       "turnover_rs",
    "TtlNbOfTxsExctd": "total_trades",
    "TradDt":          "trade_date_raw",
}


class NSEBhavCopyConnector(BaseConnector):

    source_id         = "nse_bhavcopy"
    domain            = "market_data"
    refresh_frequency = "daily"
    required_columns  = ["isin", "trade_date", "open", "high", "low", "close", "volume"]
    expected_columns  = list(_COL_MAP.keys())  # UDiFF format

    _URL_NEW = (
        "https://nsearchives.nseindia.com/content/cm"
        "/BhavCopy_NSE_CM_0_0_0_{date}_F_0000.csv.zip"
    )
    _URL_OLD = (
        "https://nsearchives.nseindia.com/content/historical/EQUITIES"
        "/{year}/{month}/cm{dd}{month}{year}bhav.csv.zip"
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
                logger.info("[nse_bhavcopy] GET %s", url)
                resp = self._get(url)
                return self._parse(resp.content, target_date)
            except Exception as e:
                if "404" in str(e):
                    logger.info("[nse_bhavcopy] 404 on %s trying next", url)
                    continue
                raise
        from src.connectors.base import SourceUnavailableError
        raise SourceUnavailableError(f"[nse_bhavcopy] No data for {target_date}")

    def backfill(self, start: date, end: date) -> Iterator[pd.DataFrame]:
        current = start
        while current <= end:
            if current.weekday() < 5:
                try:
                    yield self.fetch(current)
                except SourceUnavailableError as e:
                    logger.warning("[nse_bhavcopy] Skip %s: %s", current, e)
                self._polite_sleep(1.2)
            current += timedelta(days=1)

    def _parse(self, content: bytes, target_date: date) -> pd.DataFrame:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                csv_name = next(n for n in z.namelist() if n.endswith(".csv"))
                with z.open(csv_name) as f:
                    raw = pd.read_csv(f, dtype=str)
        except Exception as e:
            raise SourceUnavailableError(f"[nse_bhavcopy] Parse failed {target_date}: {e}") from e

        raw.columns = raw.columns.str.strip()
        # Map columns ? handle both old and new format
        df = raw.rename(columns={k: v for k, v in _COL_MAP.items() if k in raw.columns})

        if "series" in df.columns:
            df = df[df["series"].isin(_EQUITY_SERIES)].copy()

        for col in ["open", "high", "low", "close", "last_price", "prev_close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("Int64")

        if "turnover_lakhs" in df.columns:
            df["turnover_cr"] = pd.to_numeric(df["turnover_lakhs"], errors="coerce") / 100.0

        df["trade_date"] = target_date
        df["source"]     = "NSE"
        df["vwap"]       = None  # filled from nse_delivery connector

        keep = ["isin", "nse_symbol", "series", "trade_date", "source",
                "open", "high", "low", "close", "vwap",
                "volume", "turnover_cr", "total_trades", "prev_close"]

        return df[[c for c in keep if c in df.columns]].reset_index(drop=True)
