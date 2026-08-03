"""investMITRA — NSE F&O Flow (GitHub Actions + Prefect)"""
from __future__ import annotations
import logging
import os
from datetime import date, datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def ingest_nse_fo_bhavcopy():
    from src.connectors.nse_fo_bhavcopy import NSEFOBhavCopyConnector
    from src.transforms.lake_writer import write_to_lake
    from src.quality.db_logger import log_pipeline_run

    date_str = os.getenv("TRADE_DATE", "")
    target_date = date.fromisoformat(date_str) if date_str else datetime.now(IST).date()

    if target_date.weekday() >= 5:
        logger.info("Weekend — no F&O data. Skipping.")
        return

    logger.info("NSE F&O ingestion for %s", target_date)
    connector = NSEFOBhavCopyConnector()
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    try:
        df, result = connector.ingest(target_date)
    except Exception as e:
        if "404" in str(e):
            logger.info("404 — market holiday. Skipping.")
            return
        raise

    if df.empty:
        logger.warning("No F&O data")
        return

    write_to_lake(df=df, domain="market_data", table="fo_bhavcopy",
                  partition_date=target_date, source_id="nse_fo_bhavcopy",
                  quality_score=result.quality_score, run_id=run_id)
    log_pipeline_run(source_id="nse_fo_bhavcopy", run_date=target_date,
                     status="success", rows_ingested=len(df), quality_score=result.quality_score)
    logger.info("NSE F&O done — rows=%d", len(df))


if __name__ == "__main__":
    ingest_nse_fo_bhavcopy()
