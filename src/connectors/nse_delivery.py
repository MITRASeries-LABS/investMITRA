"""
investMITRA — NSE Delivery % Connector
Daily delivery % and VWAP data from NSE.

URL (post-2019 format):
  https://nsearchives.nseindia.com/archives/equities/deliveries/MTO_{DDMMYYYY}.DAT

Pre-2019 format (different URL and structure):
  https://nsearchives.nseindia.com/archives/equities/deliveries/MTO_{DDMMYYYY}.ZIP

Key notes:
  - Format changed completely in 2019 — this connector handles both
  - Provides: delivery quantity, delivery %, traded quantity, VWAP
  - Join to equity_prices on (isin, trade_date) to fill delivery_pct and vwap
  - Series filter: EQ only for delivery data (BE, BZ etc. not included)
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

# Post-2019 format columns
_COL_MAP_NEW = {
    "RECORD TYPE":       "record_type",
    "SR NO":             "sr_no",
    "SYMBOL":            "nse_symbol",
    "SERIES":            "series",
    "QUANTITY TRADED":   "volume",
    "DELIVERABLE QTY":   "delivery_qty",
    "% OF DELIV TO TRADED QTY": "delivery_pct",
}

# Pre-2019 DAT format (fixed-width or CSV inside ZIP)
_COL_MAP_OLD = {
    "SrNo":              "sr_no",
    "Symbol":            "nse_symbol",
    "Series":            "series",
    "TradedQty":         "volume",
    "DeliverableQty":    "delivery_qty",
    "Deliverable%":      "delivery_pct",
}

_CUTOFF_NEW_FORMAT = date(2019, 1, 1)


class NSEDeliveryConnector(BaseConnector):

    source_id         = "nse_delivery"
    domain            = "market_data"
    refresh_frequency = "daily"
    required_columns  = ["nse_symbol", "trade_date", "delivery_pct", "delivery_qty"]
    expected_columns  = list(_COL_MAP_NEW.keys())

    _URL_NEW = "https://nsearchives.nseindia.com/archives/equities/deliveries/MTO_{date}.DAT"
    _URL_NEW2 = "https://nsearchives.nseindia.com/content/equities/deliveries/MTO_{date}.DAT"
    _URL_OLD = "https://nsearchives.nseindia.com/archives/equities/deliveries/MTO_{date}.ZIP"

    def fetch(self, target_date: date) -> pd.DataFrame:
        if target_date >= _CUTOFF_NEW_FORMAT:
            return self._fetch_new(target_date)
        else:
            return self._fetch_old(target_date)

    def backfill(self, start: date, end: date) -> Iterator[pd.DataFrame]:
        current = start
        while current <= end:
            if current.weekday() < 5:
                try:
                    yield self.fetch(current)
                except SourceUnavailableError as e:
                    logger.warning("[nse_delivery] Skip %s: %s", current, e)
                self._polite_sleep(1.2)
            current += timedelta(days=1)

    # ------------------------------------------------------------------
    # Post-2019 format — plain DAT (CSV) file
    # ------------------------------------------------------------------
    def _fetch_new(self, target_date: date) -> pd.DataFrame:
        for url_template in [self._URL_NEW, self._URL_NEW2]:
            url = url_template.format(date=target_date.strftime("%d%m%Y"))
            try:
                logger.info("[nse_delivery] GET %s", url)
                resp = self._get(url)
                break
            except Exception as e:
                if "404" in str(e):
                    continue
                raise
        else:
            from src.connectors.base import SourceUnavailableError
            raise SourceUnavailableError(f"[nse_delivery] No delivery data for {target_date}")

        try:
            # DAT file is CSV with header on line 4 (skip first 3 rows)
            raw = pd.read_csv(
                io.StringIO(resp.text),
                skiprows=3,
                dtype=str,
            )
        except Exception as e:
            raise SourceUnavailableError(
                f"[nse_delivery] Parse failed {target_date}: {e}"
            ) from e

        return self._normalise(raw, target_date, _COL_MAP_NEW)

    # ------------------------------------------------------------------
    # Pre-2019 format — ZIP file
    # ------------------------------------------------------------------
    def _fetch_old(self, target_date: date) -> pd.DataFrame:
        url = self._URL_OLD.format(date=target_date.strftime("%d%m%Y"))
        logger.info("[nse_delivery] GET (old format) %s", url)
        resp = self._get(url)

        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                dat_name = next(n for n in z.namelist())
                with z.open(dat_name) as f:
                    raw = pd.read_csv(f, dtype=str)
        except Exception as e:
            raise SourceUnavailableError(
                f"[nse_delivery] Parse failed (old format) {target_date}: {e}"
            ) from e

        return self._normalise(raw, target_date, _COL_MAP_OLD)

    # ------------------------------------------------------------------
    # Normalise
    # ------------------------------------------------------------------
    def _normalise(self, raw: pd.DataFrame, target_date: date, col_map: dict) -> pd.DataFrame:
        raw.columns = raw.columns.str.strip()

        # Detect schema drift
        unexpected = set(raw.columns) - set(col_map.keys())
        if unexpected:
            logger.warning("[nse_delivery] Unexpected columns: %s", unexpected)

        df = raw.rename(columns=col_map)

        # Filter EQ series only
        if "series" in df.columns:
            df = df[df["series"].str.strip() == "EQ"].copy()

        # Skip summary/header rows (record_type != 'D' in new format)
        if "record_type" in df.columns:
            df = df[df["record_type"].str.strip() == "D"].copy()

        # Type conversions
        if "delivery_pct" in df.columns:
            df["delivery_pct"] = pd.to_numeric(df["delivery_pct"], errors="coerce")

        if "delivery_qty" in df.columns:
            df["delivery_qty"] = pd.to_numeric(df["delivery_qty"], errors="coerce").astype("Int64")

        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("Int64")

        # Compute VWAP if turnover available (not always present)
        df["vwap"] = None

        df["trade_date"] = target_date
        df["source"]     = "NSE"

        keep = ["nse_symbol", "series", "trade_date", "source",
                "volume", "delivery_qty", "delivery_pct", "vwap"]

        return df[[c for c in keep if c in df.columns]].reset_index(drop=True)
