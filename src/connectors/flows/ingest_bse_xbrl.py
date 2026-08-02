"""
investMITRA — BSE XBRL Flow
Runs via GitHub Actions (company_financials.yml)
Not a Prefect deployment — called as: python -m src.connectors.flows.ingest_bse_xbrl
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime

from src.connectors.bse_xbrl import BSEXBRLConnector
from src.transforms.lake_writer import write_to_lake
from src.quality.db_logger import log_pipeline_run
from src.config.database import get_pg_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def ingest_bse_xbrl(mode: str = "daily"):
    """
    mode = "daily"    → fetch recent filings only
    mode = "backfill" → full historical backfill (slow — run once)
    """
    logger.info("BSE XBRL ingestion started — mode=%s", mode)
    connector = BSEXBRLConnector()
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    target_date = date.today()

    df, result = connector.ingest(target_date)

    if df.empty:
        logger.warning("No data returned — skipping write")
        return

    path = write_to_lake(
        df=df,
        domain="company_financials",
        table="quarterly_results",
        partition_date=target_date,
        source_id="bse_xbrl",
        quality_score=result.quality_score,
        run_id=run_id,
    )

    # Write to Neon company_financials table
    _write_to_db(df)

    log_pipeline_run(
        source_id="bse_xbrl",
        run_date=target_date,
        status="success" if result.quality_score >= 50 else "quarantined",
        rows_ingested=len(df),
        quality_score=result.quality_score,
    )

    logger.info("BSE XBRL done — rows=%d quality=%d path=%s",
                len(df), result.quality_score, path)


def _write_to_db(df):
    """Upsert financial results into Neon company_financials table."""
    with get_pg_conn() as conn:
        cur = conn.cursor()
        written = 0
        for _, row in df.iterrows():
            try:
                cur.execute(
                    """
                    INSERT INTO investmitra.company_financials
                        (isin, period_end, period_type, filing_date,
                         revenue_cr, ebitda_cr, ebit_cr, pat_cr, eps,
                         is_consolidated, taxonomy, source_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (isin, period_end, period_type, source_id)
                    DO UPDATE SET
                        pat_cr     = EXCLUDED.pat_cr,
                        revenue_cr = EXCLUDED.revenue_cr,
                        filing_date= EXCLUDED.filing_date
                    """,
                    (
                        row.get("isin"),
                        row.get("period_end"),
                        row.get("period_type"),
                        row.get("filing_date"),
                        row.get("revenue_cr"),
                        row.get("ebitda_cr"),
                        row.get("ebit_cr"),
                        row.get("pat_cr"),
                        row.get("eps"),
                        row.get("is_consolidated", True),
                        row.get("taxonomy", "IFRS"),
                        "bse_xbrl",
                    ),
                )
                written += 1
            except Exception as e:
                logger.warning("DB write failed for %s: %s", row.get("isin"), e)
        cur.close()
        logger.info("Wrote %d rows to company_financials", written)


if __name__ == "__main__":
    mode = os.getenv("RUN_MODE", "daily")
    ingest_bse_xbrl(mode=mode)
