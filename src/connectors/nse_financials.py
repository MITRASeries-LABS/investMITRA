"""
investMITRA — NSE Financial Results API Connector
Quarterly financial results from NSE JSON API.

Used to cross-validate BSE XBRL figures.
NSE API is cleaner JSON but has less historical depth than BSE XBRL.

URL:
  https://www.nseindia.com/api/results-comparator?index=equities&period=Quarterly&symbol={symbol}
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Iterator, Optional

import pandas as pd

from .base import BaseConnector, SourceUnavailableError

logger = logging.getLogger(__name__)

_NSE_RESULTS_URL = (
    "https://www.nseindia.com/api/results-comparator"
    "?index=equities&period={period}&symbol={symbol}"
)


class NSEFinancialsConnector(BaseConnector):

    source_id         = "nse_financials_api"
    domain            = "company_financials"
    refresh_frequency = "quarterly"
    required_columns  = ["isin", "period_end", "filing_date", "revenue_cr", "pat_cr"]
    expected_columns  = []

    def fetch(self, target_date: date) -> pd.DataFrame:
        """Fetch recent quarterly results for all active NSE symbols."""
        symbols = self._get_active_symbols()
        if not symbols:
            logger.warning("[nse_financials] No symbols in company_master")
            return pd.DataFrame()

        all_rows = []
        for symbol, isin in symbols[:50]:
            try:
                rows = self._fetch_for_symbol(symbol, isin)
                all_rows.extend(rows)
                self._polite_sleep(1.0)  # NSE rate limits aggressively
            except Exception as e:
                logger.warning("[nse_financials] Failed for %s: %s", symbol, e)

        return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()

    def backfill(self, start: date, end: date) -> Iterator[pd.DataFrame]:
        current = start
        while current <= end:
            yield self.fetch(current)
            current = date(
                current.year + (current.month + 3) // 12,
                (current.month + 3) % 12 or 12, 1
            )
            self._polite_sleep(2.0)

    def _fetch_for_symbol(self, symbol: str, isin: str) -> list[dict]:
        # NSE requires a session cookie — visit homepage first
        self._session.get("https://www.nseindia.com", timeout=10)

        url = _NSE_RESULTS_URL.format(period="Quarterly", symbol=symbol)
        try:
            resp = self._get(url)
            data = resp.json()
        except Exception as e:
            raise SourceUnavailableError(f"NSE API failed for {symbol}: {e}")

        rows = []
        for item in data.get("data", []):
            try:
                row = self._parse_row(item, isin, symbol)
                if row:
                    rows.append(row)
            except Exception as e:
                logger.debug("[nse_financials] Parse error %s: %s", symbol, e)

        return rows

    def _parse_row(self, item: dict, isin: str, symbol: str) -> Optional[dict]:
        period_str = item.get("period") or item.get("toDate")
        if not period_str:
            return None

        try:
            period_end = pd.to_datetime(period_str).date()
        except Exception:
            return None

        filing_str = item.get("xbrlAttachment") or item.get("broadCastDate")
        try:
            filing_date = pd.to_datetime(filing_str).date()
        except Exception:
            filing_date = period_end + timedelta(days=45)

        def to_cr(val) -> Optional[float]:
            try:
                return round(float(str(val).replace(",", "")), 4)
            except Exception:
                return None

        return {
            "isin":           isin,
            "nse_symbol":     symbol,
            "period_end":     period_end,
            "period_type":    self._quarter_label(period_end),
            "filing_date":    filing_date,
            "revenue_cr":     to_cr(item.get("income") or item.get("netSales")),
            "pat_cr":         to_cr(item.get("profit") or item.get("netProfit")),
            "eps":            self._safe_float(item.get("eps")),
            "is_consolidated": str(item.get("consolidated", "")).lower() == "yes",
            "taxonomy":       "IFRS" if period_end >= date(2016, 4, 1) else "IND_GAAP",
            "source_id":      self.source_id,
        }

    def _quarter_label(self, period_end: date) -> str:
        month = period_end.month
        if month in (4, 5, 6):    return "Q1"
        if month in (7, 8, 9):    return "Q2"
        if month in (10, 11, 12): return "Q3"
        return "Q4"

    def _safe_float(self, val) -> Optional[float]:
        try:
            return float(str(val).replace(",", ""))
        except Exception:
            return None

    def _get_active_symbols(self) -> list[tuple[str, str]]:
        try:
            from src.config.database import get_pg_conn
            with get_pg_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT nse_symbol, isin
                    FROM investmitra.company_master
                    WHERE is_active = TRUE
                      AND nse_symbol IS NOT NULL
                    ORDER BY isin
                    """
                )
                return cur.fetchall()
        except Exception as e:
            logger.error("[nse_financials] Could not fetch symbols: %s", e)
            return []
