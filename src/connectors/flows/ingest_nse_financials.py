"""
investMITRA — NSE Financial Results Flow
Runs via GitHub Actions (company_financials.yml)
Called as: python -m src.connectors.flows.ingest_nse_financials
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime

from src.connectors.nse_financials import NSEFinancialsConnector
from src.transforms.lake_writer import write_to_lake
from src.quality.db_logger import log_pipeline_run
from src.config.database import get_pg_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def ingest_nse_financials():
    logger.info("NSE Financials API ingestion started")
    connector = NSEFinancialsConnector()
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    target_date = date.today()

    df, result = connector.ingest(target_date)

    if df.empty:
        logger.warning("No data returned")
        return

    write_to_lake(
        df=df,
        domain="company_financials",
        table="nse_quarterly_results",
        partition_date=target_date,
        source_id="nse_financials_api",
        quality_score=result.quality_score,
        run_id=run_id,
    )

    _write_to_db(df)

    log_pipeline_run(
        source_id="nse_financials_api",
        run_date=target_date,
        status="success" if result.quality_score >= 50 else "quarantined",
        rows_ingested=len(df),
        quality_score=result.quality_score,
    )

    logger.info("NSE Financials done — rows=%d quality=%d", len(df), result.quality_score)


def _write_to_db(df):
    with get_pg_conn() as conn:
        cur = conn.cursor()
        written = 0
        for _, row in df.iterrows():
            try:
                cur.execute(
                    """
                    INSERT INTO investmitra.company_financials
                        (isin, period_end, period_type, filing_date,
                         revenue_cr, pat_cr, eps, is_consolidated,
                         taxonomy, source_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (isin, period_end, period_type, source_id)
                    DO UPDATE SET
                        pat_cr     = EXCLUDED.pat_cr,
                        revenue_cr = EXCLUDED.revenue_cr,
                        filing_date= EXCLUDED.filing_date
                    """,
                    (
                        row.get("isin"), row.get("period_end"),
                        row.get("period_type"), row.get("filing_date"),
                        row.get("revenue_cr"), row.get("pat_cr"),
                        row.get("eps"), row.get("is_consolidated", True),
                        row.get("taxonomy", "IFRS"), "nse_financials_api",
                    ),
                )
                written += 1
            except Exception as e:
                logger.warning("DB write failed for %s: %s", row.get("isin"), e)
        cur.close()
        logger.info("Wrote %d rows to company_financials", written)


if __name__ == "__main__":
    ingest_nse_financials()
