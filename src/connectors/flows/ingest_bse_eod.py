"""investMITRA — BSE EOD Flow (GitHub Actions + Prefect)"""
from __future__ import annotations
import logging
import os
from datetime import date, datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

def ingest_bse_eod():
    from src.connectors.bse_eod import BSEEODConnector
    from src.transforms.lake_writer import write_to_lake
    from src.quality.db_logger import log_pipeline_run

    date_str = os.getenv("TRADE_DATE", "")
    target_date = date.fromisoformat(date_str) if date_str else date.today()

    logger.info("BSE EOD ingestion for %s", target_date)
    connector = BSEEODConnector()
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    df, result = connector.ingest(target_date)
    if df.empty:
        logger.warning("No BSE data")
        return
    write_to_lake(df=df, domain="market_data", table="equity_prices",
                  partition_date=target_date, source_id="bse_eod",
                  quality_score=result.quality_score, run_id=run_id)
    log_pipeline_run(source_id="bse_eod", run_date=target_date,
                     status="success", rows_ingested=len(df), quality_score=result.quality_score)
    logger.info("BSE EOD done — rows=%d", len(df))

if __name__ == "__main__":
    ingest_bse_eod()
