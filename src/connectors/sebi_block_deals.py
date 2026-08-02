"""
investMITRA — SEBI Block/Bulk Deals Connector
Daily block and bulk deal disclosures from NSE.

URL:
  https://www.nseindia.com/api/block-deal?index=equities
  https://www.nseindia.com/api/bulk-deal?index=equities

Key notes:
  - Block deal: >500,000 shares or >5 Crore value in single trade
  - Bulk deal: >0.5% of total equity in a single day
  - Strong institutional signal — who is buying/selling large blocks
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Iterator

import pandas as pd

from .base import BaseConnector, SourceUnavailableError

logger = logging.getLogger(__name__)


class SEBIBlockDealsConnector(BaseConnector):

    source_id         = "sebi_block_deals"
    domain            = "ownership"
    refresh_frequency = "daily"
    required_columns  = ["isin", "trade_date", "client_name", "quantity", "deal_type"]
    expected_columns  = []

    _BLOCK_URL = "https://www.nseindia.com/api/block-deal?index=equities"
    _BULK_URL  = "https://www.nseindia.com/api/bulk-deal?index=equities"

    def fetch(self, target_date: date) -> pd.DataFrame:
        # NSE requires session cookie
        self._session.get("https://www.nseindia.com", timeout=10)

        all_rows = []
        for url, deal_type in [(self._BLOCK_URL, "BLOCK"), (self._BULK_URL, "BULK")]:
            try:
                resp = self._get(url)
                data = resp.json()
                for item in data.get("data", []):
                    row = self._parse_row(item, deal_type, target_date)
                    if row:
                        all_rows.append(row)
            except Exception as e:
                logger.warning("[sebi_block_deals] Failed %s: %s", deal_type, e)
            self._polite_sleep(1.0)

        return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()

    def backfill(self, start: date, end: date) -> Iterator[pd.DataFrame]:
        current = start
        while current <= end:
            if current.weekday() < 5:
                try:
                    yield self.fetch(current)
                except Exception as e:
                    logger.warning("[sebi_block_deals] Skip %s: %s", current, e)
                self._polite_sleep(1.5)
            current += timedelta(days=1)

    def _parse_row(self, item: dict, deal_type: str, trade_date: date):
        symbol = item.get("symbol") or item.get("Symbol")
        if not symbol:
            return None

        isin = self._symbol_to_isin(symbol)

        def safe_float(val):
            try: return float(str(val).replace(",", ""))
            except: return None

        def safe_int(val):
            try: return int(str(val).replace(",", ""))
            except: return None

        return {
            "isin":        isin,
            "nse_symbol":  symbol,
            "trade_date":  trade_date,
            "client_name": item.get("clientName") or item.get("ClientName"),
            "buy_sell":    str(item.get("buySell", "")).upper(),
            "quantity":    safe_int(item.get("quantity") or item.get("Quantity")),
            "price":       safe_float(item.get("price") or item.get("Price")),
            "value_cr":    safe_float(item.get("value")),
            "deal_type":   deal_type,
            "source_id":   self.source_id,
        }

    def _symbol_to_isin(self, symbol: str):
        """Look up ISIN for a symbol from company_master."""
        try:
            from src.config.database import get_pg_conn
            with get_pg_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT isin FROM investmitra.company_master WHERE nse_symbol = %s",
                    (symbol,)
                )
                row = cur.fetchone()
                return row[0] if row else None
        except Exception:
            return None
