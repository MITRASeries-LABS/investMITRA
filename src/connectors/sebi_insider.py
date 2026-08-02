"""
investMITRA — SEBI Insider Trading Connector
Insider trading disclosures from SEBI/NSE/BSE.

URL (NSE):
  https://www.nseindia.com/api/corporates-pit?index=equities&symbol={symbol}

Key notes:
  - High-signal event: promoter/director buying = bullish, selling = bearish
  - Filter by transaction_type: acquisition vs disposal
  - Link to company via ISIN
  - Store raw — NER pipeline links entity names to ISINs
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Iterator, Optional

import pandas as pd

from .base import BaseConnector, SourceUnavailableError

logger = logging.getLogger(__name__)

_NSE_INSIDER_URL = (
    "https://www.nseindia.com/api/corporates-pit"
    "?index=equities&symbol={symbol}&from_date={from_date}&to_date={to_date}"
)


class SEBIInsiderConnector(BaseConnector):

    source_id         = "sebi_insider"
    domain            = "ownership"
    refresh_frequency = "event"
    required_columns  = ["isin", "transaction_date", "transaction_type", "quantity"]
    expected_columns  = []

    def fetch(self, target_date: date) -> pd.DataFrame:
        """Fetch insider trades for the past 7 days."""
        from_date = target_date - timedelta(days=7)
        symbols   = self._get_active_symbols()
        all_rows  = []

        # First visit NSE homepage to get session cookie
        self._session.get("https://www.nseindia.com", timeout=10)

        for symbol, isin in symbols[:200]:
            try:
                rows = self._fetch_for_symbol(symbol, isin, from_date, target_date)
                all_rows.extend(rows)
                self._polite_sleep(1.0)
            except Exception as e:
                logger.warning("[sebi_insider] Failed %s: %s", symbol, e)

        return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()

    def backfill(self, start: date, end: date) -> Iterator[pd.DataFrame]:
        current = start
        while current <= end:
            yield self.fetch(current)
            current += timedelta(days=7)
            self._polite_sleep(2.0)

    def _fetch_for_symbol(
        self, symbol: str, isin: str, from_date: date, to_date: date
    ) -> list[dict]:
        url = _NSE_INSIDER_URL.format(
            symbol=symbol,
            from_date=from_date.strftime("%d-%m-%Y"),
            to_date=to_date.strftime("%d-%m-%Y"),
        )
        try:
            resp = self._get(url)
            data = resp.json()
        except Exception as e:
            raise SourceUnavailableError(f"NSE insider API failed {symbol}: {e}")

        rows = []
        for item in data.get("data", []):
            try:
                row = self._parse_row(item, isin)
                if row:
                    rows.append(row)
            except Exception as e:
                logger.debug("[sebi_insider] Parse error %s: %s", symbol, e)

        return rows

    def _parse_row(self, item: dict, isin: str) -> Optional[dict]:
        date_str = item.get("date") or item.get("acqfromDt")
        if not date_str:
            return None

        try:
            transaction_date = pd.to_datetime(date_str, dayfirst=True).date()
        except Exception:
            return None

        transaction_type = str(item.get("buyOrSell", "")).upper()
        if transaction_type not in ("BUY", "SELL", "ACQUISITION", "DISPOSAL"):
            transaction_type = "UNKNOWN"

        def safe_float(val):
            try:
                return float(str(val).replace(",", ""))
            except Exception:
                return None

        def safe_int(val):
            try:
                return int(str(val).replace(",", ""))
            except Exception:
                return None

        return {
            "isin":             isin,
            "person_name":      item.get("personName") or item.get("acqName"),
            "person_category":  item.get("personCategory") or item.get("category"),
            "transaction_date": transaction_date,
            "transaction_type": transaction_type,
            "quantity":         safe_int(item.get("secAcq") or item.get("noOfShares")),
            "value_cr":         safe_float(item.get("value")),
            "price":            safe_float(item.get("price") or item.get("acqPrice")),
            "post_holding_pct": safe_float(item.get("afterShareHoldPercentage")),
            "source_id":        self.source_id,
        }

    def _get_active_symbols(self) -> list[tuple[str, str]]:
        try:
            from src.config.database import get_pg_conn
            with get_pg_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT nse_symbol, isin FROM investmitra.company_master
                    WHERE is_active = TRUE AND nse_symbol IS NOT NULL
                    ORDER BY market_cap_category, isin
                    """
                )
                return cur.fetchall()
        except Exception as e:
            logger.error("[sebi_insider] DB error: %s", e)
            return []
