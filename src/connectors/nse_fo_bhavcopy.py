"""
investMITRA — NSE F&O Bhavcopy Connector
Daily Futures & Options OHLCV + Open Interest from NSE.

New UDiFF format URL:
  https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{YYYYMMDD}_F_0000.csv.zip
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
    "FinInstrmTp":     "instrument_type",
    "TckrSymb":        "nse_symbol",
    "ISIN":            "isin",
    "XpryDt":          "expiry_date",
    "StrkPric":        "strike_price",
    "OptnTp":          "option_type",
    "OpnPric":         "open",
    "HghPric":         "high",
    "LwPric":          "low",
    "ClsPric":         "close",
    "SttlmPric":       "settle_price",
    "TtlTradgVol":     "contracts",
    "TtlTrfVal":       "turnover_rs",
    "OpnIntrst":       "open_interest",
    "ChngInOpnIntrst": "oi_change",
    "TradDt":          "trade_date_raw",
    "FinInstrmNm":     "company_name",
}

_INSTRUMENT_TYPES = {
    "STO", "IDO", "STF", "IDF",
    "FF", "IO", "IF", "OI", "FS", "OS",
    "STK", "IDX", "FUTIDX", "OPTIDX", "FUTSTK", "OPTSTK"
}


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
                csv_name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
                with z.open(csv_name) as f:
                    raw = pd.read_csv(f, dtype=str, on_bad_lines="skip")
        except Exception as e:
            raise SourceUnavailableError(
                f"[nse_fo_bhavcopy] Parse failed {target_date}: {e}"
            ) from e

        raw.columns = raw.columns.str.strip()
        return self._normalise(raw, target_date)

    def _normalise(self, raw: pd.DataFrame, target_date: date) -> pd.DataFrame:
        df = raw.rename(columns={k: v for k, v in _COL_MAP.items() if k in raw.columns})

        # Filter to known instrument types
        if "instrument_type" in df.columns:
            df = df[df["instrument_type"].isin(_INSTRUMENT_TYPES)].copy()

        # Price columns
        for col in ["open", "high", "low", "close", "settle_price", "strike_price"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Integer columns
        for col in ["contracts", "open_interest", "oi_change"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

        # Turnover: rupees -> crore
        if "turnover_rs" in df.columns:
            df["turnover_cr"] = pd.to_numeric(df["turnover_rs"], errors="coerce") / 10000000.0

        # Expiry date
        if "expiry_date" in df.columns:
            df["expiry_date"] = pd.to_datetime(
                df["expiry_date"], errors="coerce"
            ).dt.date

        # Option type normalise
        if "option_type" in df.columns:
            df["option_type"] = df["option_type"].replace({"XX": None, "": None})

        df["trade_date"] = target_date
        df["source"]     = "NSE"

        keep = [
            "nse_symbol", "isin", "instrument_type", "expiry_date", "strike_price",
            "option_type", "trade_date", "source", "company_name",
            "open", "high", "low", "close", "settle_price",
            "contracts", "turnover_cr", "open_interest", "oi_change",
        ]

        return df[[c for c in keep if c in df.columns]].reset_index(drop=True)