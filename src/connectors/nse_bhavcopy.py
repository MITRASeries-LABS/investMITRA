"""
investMITRA — NSE Bhavcopy Connector (v2)
Uses the full security deliverable data file which includes:
- OHLCV prices
- Delivery quantity and delivery %
- Average price (VWAP)
- Number of trades

New URL format (post July 2024 UDiFF migration):
  https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{DDMMYYYY}.csv

Fallback URL (older UDiFF format):
  https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{YYYYMMDD}_F_0000.csv.zip
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

# Full Bhavcopy + Delivery file (preferred)
_COL_MAP_FULL = {
    "SYMBOL":        "nse_symbol",
    "SERIES":        "series",
    "DATE1":         "trade_date_raw",
    "PREV_CLOSE":    "prev_close",
    "OPEN_PRICE":    "open",
    "HIGH_PRICE":    "high",
    "LOW_PRICE":     "low",
    "LAST_PRICE":    "last_price",
    "CLOSE_PRICE":   "close",
    "AVG_PRICE":     "vwap",
    "TTL_TRD_QNTY":  "volume",
    "TURNOVER_LACS": "turnover_lacs",
    "NO_OF_TRADES":  "total_trades",
    "DELIV_QTY":     "delivery_qty",
    "DELIV_PER":     "delivery_pct",
}

# UDiFF format (fallback)
_COL_MAP_UDIFF = {
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

_EQUITY_SERIES = {"EQ", "BE", "BZ", "BL", "GC", "IL", "ST", "SM", "MF"}


class NSEBhavCopyConnector(BaseConnector):

    source_id         = "nse_bhavcopy"
    domain            = "market_data"
    refresh_frequency = "daily"
    required_columns  = ["nse_symbol", "trade_date", "open", "high", "low", "close", "volume"]
    expected_columns  = list(_COL_MAP_FULL.keys())

    # Full file with delivery data (preferred)
    _URL_FULL = (
        "https://nsearchives.nseindia.com/products/content"
        "/sec_bhavdata_full_{date}.csv"
    )
    # UDiFF format (fallback)
    _URL_UDIFF = (
        "https://nsearchives.nseindia.com/content/cm"
        "/BhavCopy_NSE_CM_0_0_0_{date}_F_0000.csv.zip"
    )

    def fetch(self, target_date: date) -> pd.DataFrame:
        date_ddmmyyyy = target_date.strftime("%d%m%Y")
        date_yyyymmdd = target_date.strftime("%Y%m%d")

        # Try full file first (has delivery %)
        url_full  = self._URL_FULL.format(date=date_ddmmyyyy)
        url_udiff = self._URL_UDIFF.format(date=date_yyyymmdd)

        for url, fmt in [(url_full, "full"), (url_udiff, "udiff")]:
            try:
                logger.info("[nse_bhavcopy] GET %s (%s)", url, fmt)
                resp = self._get(url)
                if fmt == "full":
                    return self._parse_full(resp.content, target_date)
                else:
                    return self._parse_udiff(resp.content, target_date)
            except Exception as e:
                if "404" in str(e) or "No data" in str(e):
                    logger.info("[nse_bhavcopy] 404 on %s, trying next...", fmt)
                    continue
                raise

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

    def _parse_full(self, content: bytes, target_date: date) -> pd.DataFrame:
        """Parse the full sec_bhavdata_full file (CSV with delivery %)."""
        try:
            raw = pd.read_csv(io.StringIO(content.decode("utf-8")), dtype=str)
        except Exception as e:
            raise SourceUnavailableError(
                f"[nse_bhavcopy] Full file parse failed {target_date}: {e}"
            ) from e

        raw.columns = raw.columns.str.strip()
        df = raw.rename(columns={k: v for k, v in _COL_MAP_FULL.items() if k in raw.columns})

        # Filter equity series
        if "series" in df.columns:
            df = df[df["series"].str.strip().isin(_EQUITY_SERIES)].copy()

        # Type conversions
        for col in ["open", "high", "low", "close", "last_price", "prev_close", "vwap"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("Int64")

        if "delivery_qty" in df.columns:
            df["delivery_qty"] = pd.to_numeric(df["delivery_qty"], errors="coerce").astype("Int64")

        if "delivery_pct" in df.columns:
            df["delivery_pct"] = pd.to_numeric(df["delivery_pct"], errors="coerce")

        if "turnover_lacs" in df.columns:
            # Turnover in lacs — convert to crore
            df["turnover_cr"] = pd.to_numeric(df["turnover_lacs"], errors="coerce") / 100.0

        df["trade_date"] = target_date
        df["source"]     = "NSE"
        df["isin"]       = None  # Not in this file — will be joined via company_master

        keep = ["isin", "nse_symbol", "series", "trade_date", "source",
                "open", "high", "low", "close", "vwap", "last_price", "prev_close",
                "volume", "delivery_qty", "delivery_pct", "turnover_cr", "total_trades"]

        return df[[c for c in keep if c in df.columns]].reset_index(drop=True)

    def _parse_udiff(self, content: bytes, target_date: date) -> pd.DataFrame:
        """Parse the UDiFF ZIP format (fallback)."""
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                csv_name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
                with z.open(csv_name) as f:
                    raw = pd.read_csv(f, dtype=str, on_bad_lines="skip")
        except Exception as e:
            raise SourceUnavailableError(
                f"[nse_bhavcopy] UDiFF parse failed {target_date}: {e}"
            ) from e

        raw.columns = raw.columns.str.strip()
        df = raw.rename(columns={k: v for k, v in _COL_MAP_UDIFF.items() if k in raw.columns})

        if "series" in df.columns:
            df = df[df["series"].str.strip().isin(_EQUITY_SERIES)].copy()

        for col in ["open", "high", "low", "close", "last_price", "prev_close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("Int64")

        if "turnover_rs" in df.columns:
            df["turnover_cr"] = pd.to_numeric(df["turnover_rs"], errors="coerce") / 10000000.0

        df["trade_date"]   = target_date
        df["source"]       = "NSE"
        df["delivery_pct"] = None
        df["vwap"]         = None

        keep = ["isin", "nse_symbol", "series", "trade_date", "source",
                "open", "high", "low", "close", "vwap",
                "volume", "turnover_cr", "total_trades", "prev_close", "delivery_pct"]

        return df[[c for c in keep if c in df.columns]].reset_index(drop=True)
