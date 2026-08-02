"""
investMITRA — BSE Shareholding Pattern Connector
Quarterly shareholding patterns filed with BSE.

URL:
  https://api.bseindia.com/BseIndiaAPI/api/ShareHoldingPatterns/w?scripcode={bse_code}

Key notes:
  - Filed within 21 days of quarter end (e.g. Q1 end Jun 30 → filed by Jul 21)
  - Always use filing_date for PIT features, not period_end
  - Promoter pledging % is a high-signal risk indicator
  - FII % changes signal institutional sentiment shifts
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Iterator, Optional

import pandas as pd

from .base import BaseConnector, SourceUnavailableError

logger = logging.getLogger(__name__)

_BSE_SHP_URL = (
    "https://api.bseindia.com/BseIndiaAPI/api/ShareHoldingPatterns/w"
    "?scripcode={bse_code}"
)


class BSEShareholdingConnector(BaseConnector):

    source_id         = "bse_shareholding"
    domain            = "ownership"
    refresh_frequency = "quarterly"
    required_columns  = ["isin", "period_end", "filing_date", "promoter_pct"]
    expected_columns  = []

    def fetch(self, target_date: date) -> pd.DataFrame:
        bse_codes = self._get_active_bse_codes()
        if not bse_codes:
            return pd.DataFrame()

        all_rows = []
        for bse_code, isin in bse_codes[:100]:
            try:
                rows = self._fetch_for_company(bse_code, isin)
                all_rows.extend(rows)
                self._polite_sleep(0.5)
            except Exception as e:
                logger.warning("[bse_shareholding] Failed %s: %s", bse_code, e)

        return pd.DataFrame(all_rows) if all_rows else pd.DataFrame()

    def backfill(self, start: date, end: date) -> Iterator[pd.DataFrame]:
        current = start
        while current <= end:
            yield self.fetch(current)
            current += timedelta(days=90)
            self._polite_sleep(2.0)

    def _fetch_for_company(self, bse_code: str, isin: str) -> list[dict]:
        url = _BSE_SHP_URL.format(bse_code=bse_code)
        try:
            resp = self._session.get(url, timeout=15, headers={
                "Referer": "https://www.bseindia.com",
                "User-Agent": "Mozilla/5.0",
            })
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            raise SourceUnavailableError(f"BSE SHP failed {bse_code}: {e}")

        rows = []
        for item in data.get("Table", []) or data.get("data", []):
            try:
                row = self._parse_row(item, isin, bse_code)
                if row:
                    rows.append(row)
            except Exception as e:
                logger.debug("[bse_shareholding] Parse error %s: %s", bse_code, e)

        return rows

    def _parse_row(self, item: dict, isin: str, bse_code: str) -> Optional[dict]:
        period_str = item.get("QUARTER") or item.get("QuarterEndDate")
        if not period_str:
            return None

        try:
            period_end = pd.to_datetime(period_str, dayfirst=True).date()
        except Exception:
            return None

        filing_str = item.get("DATE_OF_FILING") or item.get("FilingDate")
        try:
            filing_date = pd.to_datetime(filing_str, dayfirst=True).date()
        except Exception:
            filing_date = period_end + timedelta(days=21)

        def pct(val) -> Optional[float]:
            try:
                return round(float(str(val).replace(",", "")), 4)
            except Exception:
                return None

        return {
            "isin":                isin,
            "bse_code":            bse_code,
            "period_end":          period_end,
            "filing_date":         filing_date,   # ⚠️ PIT key
            "promoter_pct":        pct(item.get("PROMOTER_HOLD") or item.get("PromoterHolding")),
            "promoter_pledged_pct":pct(item.get("PROMOTER_PLEDGE") or item.get("PledgedShares")),
            "fii_pct":             pct(item.get("FII_HOLD") or item.get("FIIHolding")),
            "dii_pct":             pct(item.get("DII_HOLD") or item.get("DIIHolding")),
            "mf_pct":              pct(item.get("MF_HOLD") or item.get("MFHolding")),
            "public_pct":          pct(item.get("PUBLIC_HOLD") or item.get("PublicHolding")),
            "total_shareholders":  self._safe_int(item.get("TOTAL_HOLDERS")),
            "source_id":           self.source_id,
        }

    def _safe_int(self, val) -> Optional[int]:
        try:
            return int(str(val).replace(",", ""))
        except Exception:
            return None

    def _get_active_bse_codes(self) -> list[tuple[str, str]]:
        try:
            from src.config.database import get_pg_conn
            with get_pg_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT bse_code, isin FROM investmitra.company_master
                    WHERE is_active = TRUE AND bse_code IS NOT NULL
                    ORDER BY market_cap_category, isin
                    """
                )
                return cur.fetchall()
        except Exception as e:
            logger.error("[bse_shareholding] DB error: %s", e)
            return []
