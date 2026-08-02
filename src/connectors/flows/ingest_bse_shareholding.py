"""
investMITRA — Ownership Flows
Runs via GitHub Actions (ownership.yml)
"""

from __future__ import annotations
import logging
import os
from datetime import date, datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def ingest_bse_shareholding():
    from src.connectors.bse_shareholding import BSEShareholdingConnector
    from src.transforms.lake_writer import write_to_lake
    from src.quality.db_logger import log_pipeline_run
    from src.config.database import get_pg_conn

    logger.info("BSE Shareholding ingestion started")
    connector = BSEShareholdingConnector()
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    target_date = date.today()

    df, result = connector.ingest(target_date)
    if df.empty:
        logger.warning("No shareholding data")
        return

    write_to_lake(df=df, domain="ownership", table="shareholding_patterns",
                  partition_date=target_date, source_id="bse_shareholding",
                  quality_score=result.quality_score, run_id=run_id)

    # Write to Neon ownership_data table
    with get_pg_conn() as conn:
        cur = conn.cursor()
        for _, row in df.iterrows():
            try:
                cur.execute(
                    """
                    INSERT INTO investmitra.ownership_data
                        (isin, period_end, filing_date, promoter_pct,
                         promoter_pledged_pct, fii_pct, dii_pct, mf_pct,
                         public_pct, total_shareholders, source_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (isin, period_end, source_id) DO UPDATE SET
                        promoter_pct = EXCLUDED.promoter_pct,
                        filing_date  = EXCLUDED.filing_date
                    """,
                    (row.get("isin"), row.get("period_end"), row.get("filing_date"),
                     row.get("promoter_pct"), row.get("promoter_pledged_pct"),
                     row.get("fii_pct"), row.get("dii_pct"), row.get("mf_pct"),
                     row.get("public_pct"), row.get("total_shareholders"),
                     "bse_shareholding"),
                )
            except Exception as e:
                logger.warning("DB write failed %s: %s", row.get("isin"), e)
        cur.close()

    log_pipeline_run(source_id="bse_shareholding", run_date=target_date,
                     status="success", rows_ingested=len(df),
                     quality_score=result.quality_score)
    logger.info("BSE Shareholding done — rows=%d", len(df))


def ingest_sebi_insider():
    from src.connectors.sebi_insider import SEBIInsiderConnector
    from src.transforms.lake_writer import write_to_lake
    from src.quality.db_logger import log_pipeline_run

    logger.info("SEBI Insider Trading ingestion started")
    connector = SEBIInsiderConnector()
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    target_date = date.today()

    df, result = connector.ingest(target_date)
    if df.empty:
        logger.warning("No insider trading data")
        return

    write_to_lake(df=df, domain="ownership", table="insider_trades",
                  partition_date=target_date, source_id="sebi_insider",
                  quality_score=result.quality_score, run_id=run_id)

    log_pipeline_run(source_id="sebi_insider", run_date=target_date,
                     status="success", rows_ingested=len(df),
                     quality_score=result.quality_score)
    logger.info("SEBI Insider done — rows=%d", len(df))


def ingest_sebi_block_deals():
    from src.connectors.sebi_block_deals import SEBIBlockDealsConnector
    from src.transforms.lake_writer import write_to_lake
    from src.quality.db_logger import log_pipeline_run

    logger.info("SEBI Block/Bulk Deals ingestion started")
    connector = SEBIBlockDealsConnector()
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    target_date = date.today()

    df, result = connector.ingest(target_date)
    if df.empty:
        logger.warning("No block/bulk deals data")
        return

    write_to_lake(df=df, domain="ownership", table="block_bulk_deals",
                  partition_date=target_date, source_id="sebi_block_deals",
                  quality_score=result.quality_score, run_id=run_id)

    log_pipeline_run(source_id="sebi_block_deals", run_date=target_date,
                     status="success", rows_ingested=len(df),
                     quality_score=result.quality_score)
    logger.info("SEBI Block Deals done — rows=%d", len(df))


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    if target in ("all", "shareholding"):
        ingest_bse_shareholding()
    if target in ("all", "insider"):
        ingest_sebi_insider()
    if target in ("all", "block_deals"):
        ingest_sebi_block_deals()
