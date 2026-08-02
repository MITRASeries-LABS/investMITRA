"""
investMITRA — BSE XBRL Connector
Quarterly financial filings from BSE XBRL portal.

Sources:
  Post-2016: IFRS taxonomy (XBRL)
  Pre-2016:  Indian GAAP taxonomy (XBRL, different field names)
  Pre-2012:  PDF fallback (pdfplumber extraction)

BSE XBRL listing URL:
  https://www.bseindia.com/corporates/List_Scrips.html

Individual filing URL:
  https://www.bseindia.com/xml-data/corpfiling/AttachLive/{filename}.zip

Key notes:
  - Always use filing_date (when filed), NOT period_end (quarter end) for PIT features
  - Cross-validate P&L figures against NSE Financial Results API
  - Scaling: BSE filings sometimes mix lakhs and crores — detect and normalise
  - ISIN is the join key — map BSE code to ISIN via company_master
"""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import date, datetime, timedelta
from typing import Iterator, Optional

import pandas as pd
import requests

from .base import BaseConnector, SourceUnavailableError

logger = logging.getLogger(__name__)

# BSE financial results API endpoint
_BSE_RESULTS_URL = (
    "https://api.bseindia.com/BseIndiaAPI/api/StockReachGraph/w"
    "?scripcode={bse_code}&type=QB&yearfrom={year_from}&yearto={year_to}"
)

# BSE XBRL filing search
_BSE_XBRL_SEARCH = (
    "https://www.bseindia.com/corporates/ann.html"
    "?expandable=0&scrip={bse_code}&dur=Q&type=Result"
)

# Taxonomy field mappings
_IFRS_FIELDS = {
    "RevenueFromOperations":           "revenue_cr",
    "ProfitBeforeTax":                 "ebit_cr",
    "ProfitLoss":                      "pat_cr",
    "EarningsPerShareBasic":           "eps",
    "TotalAssets":                     "total_assets_cr",
    "TotalEquity":                     "equity_cr",
    "BorrowingsNoncurrent":            "total_debt_cr",
    "CashAndCashEquivalents":          "cash_cr",
    "NetCashFlowsFromOperations":      "cfo_cr",
    "PurchaseOfPropertyPlantEquipment":"capex_cr",
}

_GAAP_FIELDS = {
    "NetSalesRevenue":                 "revenue_cr",
    "ProfitBeforeTax":                 "ebit_cr",
    "ProfitAfterTax":                  "pat_cr",
    "EarningsPerShare":                "eps",
    "TotalAssets":                     "total_assets_cr",
    "ShareholdersEquity":              "equity_cr",
    "TotalDebt":                       "total_debt_cr",
    "CashAndBankBalance":              "cash_cr",
    "CashFromOperations":              "cfo_cr",
    "CapitalExpenditure":              "capex_cr",
}


class BSEXBRLConnector(BaseConnector):

    source_id         = "bse_xbrl"
    domain            = "company_financials"
    refresh_frequency = "quarterly"
    required_columns  = ["isin", "period_end", "filing_date", "revenue_cr", "pat_cr"]
    expected_columns  = []  # XBRL schema varies — drift detection handled differently

    # BSE financial results JSON API (simpler than XBRL parsing)
    _RESULTS_API = (
        "https://api.bseindia.com/BseIndiaAPI/api/StockReachGraph/w"
        "?scripcode={bse_code}&type=QB"
    )

    def fetch(self, target_date: date) -> pd.DataFrame:
        """
        Fetch recent quarterly results for all active companies.
        Uses BSE JSON API as primary source (simpler than raw XBRL).
        Falls back to XBRL parsing for pre-2016 data.
        """
        logger.info("[bse_xbrl] Fetching filings around %s", target_date)

        # Get active BSE codes from company_master
        bse_codes = self._get_active_bse_codes()
        if not bse_codes:
            logger.warning("[bse_xbrl] No BSE codes found in company_master")
            return pd.DataFrame()

        all_rows = []
        for bse_code, isin in bse_codes[:50]:  # batch of 50 per run
            try:
                rows = self._fetch_for_company(bse_code, isin, target_date)
                all_rows.extend(rows)
                self._polite_sleep(0.5)
            except Exception as e:
                logger.warning("[bse_xbrl] Failed for %s: %s", bse_code, e)

        if not all_rows:
            return pd.DataFrame(columns=self.required_columns)

        return pd.DataFrame(all_rows)

    def backfill(self, start: date, end: date) -> Iterator[pd.DataFrame]:
        """Yield quarterly batches for historical backfill."""
        current = start
        while current <= end:
            yield self.fetch(current)
            # Move to next quarter
            current = date(
                current.year + (current.month + 3) // 12,
                (current.month + 3) % 12 or 12,
                1
            )
            self._polite_sleep(2.0)

    def _fetch_for_company(
        self, bse_code: str, isin: str, target_date: date
    ) -> list[dict]:
        """Fetch quarterly results for a single company via BSE JSON API."""
        url = self._RESULTS_API.format(bse_code=bse_code)

        try:
            resp = self._session.get(url, timeout=15, headers={
                "Referer": "https://www.bseindia.com",
                "User-Agent": "Mozilla/5.0",
            })
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            raise SourceUnavailableError(f"BSE API failed for {bse_code}: {e}")

        rows = []
        for item in data.get("Table", []):
            try:
                row = self._parse_result_row(item, isin, bse_code)
                if row:
                    rows.append(row)
            except Exception as e:
                logger.debug("[bse_xbrl] Parse error for %s: %s", bse_code, e)

        return rows

    def _parse_result_row(
        self, item: dict, isin: str, bse_code: str
    ) -> Optional[dict]:
        """Parse a single quarterly result row from BSE JSON API."""
        # Extract period end date
        period_str = item.get("QUARTR") or item.get("YearEnd")
        if not period_str:
            return None

        try:
            period_end = pd.to_datetime(period_str, dayfirst=True).date()
        except Exception:
            return None

        # Filing date (when announced)
        filing_str = item.get("NEWS_DT") or item.get("AnnouncementDate")
        try:
            filing_date = pd.to_datetime(filing_str, dayfirst=True).date()
        except Exception:
            filing_date = period_end + timedelta(days=45)  # estimate if missing

        # Detect scaling — BSE mixes lakhs and crores
        scale = self._detect_scale(item)

        def to_cr(val) -> Optional[float]:
            """Convert value to crores applying scale factor."""
            try:
                v = float(str(val).replace(",", ""))
                return round(v * scale, 4)
            except Exception:
                return None

        # Period type
        period_type = "ANNUAL" if item.get("PERIOD_TYPE") == "Y" else self._quarter_label(period_end)

        return {
            "isin":           isin,
            "bse_code":       bse_code,
            "period_end":     period_end,
            "period_type":    period_type,
            "filing_date":    filing_date,  # ⚠️ PIT key
            "revenue_cr":     to_cr(item.get("NETSALES") or item.get("Revenue")),
            "ebitda_cr":      to_cr(item.get("PBDIT") or item.get("EBITDA")),
            "ebit_cr":        to_cr(item.get("PBT") or item.get("ProfitBeforeTax")),
            "pat_cr":         to_cr(item.get("NETPROFT") or item.get("PAT")),
            "eps":            self._safe_float(item.get("EPS") or item.get("BasicEPS")),
            "is_consolidated": str(item.get("FINTYPE", "")).upper() == "C",
            "taxonomy":       "IFRS" if period_end >= date(2016, 4, 1) else "IND_GAAP",
            "source_id":      self.source_id,
        }

    def _detect_scale(self, item: dict) -> float:
        """
        Detect if values are in lakhs (return 0.01) or crores (return 1.0).
        BSE filings are inconsistent — check the denomination field.
        """
        denom = str(item.get("DENOMINATION", "")).lower()
        if "lakh" in denom or "lac" in denom:
            return 0.01   # lakhs → crores
        return 1.0         # already in crores

    def _safe_float(self, val) -> Optional[float]:
        try:
            return float(str(val).replace(",", ""))
        except Exception:
            return None

    def _quarter_label(self, period_end: date) -> str:
        """Return Q1/Q2/Q3/Q4 based on Indian financial year (Apr-Mar)."""
        month = period_end.month
        if month in (4, 5, 6):   return "Q1"
        if month in (7, 8, 9):   return "Q2"
        if month in (10, 11, 12): return "Q3"
        return "Q4"

    def _get_active_bse_codes(self) -> list[tuple[str, str]]:
        """Fetch active BSE codes from company_master in Neon."""
        try:
            from src.config.database import get_pg_conn
            with get_pg_conn() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT bse_code, isin
                    FROM investmitra.company_master
                    WHERE is_active = TRUE
                      AND bse_code IS NOT NULL
                    ORDER BY isin
                    """
                )
                return cur.fetchall()
        except Exception as e:
            logger.error("[bse_xbrl] Could not fetch BSE codes: %s", e)
            return []
