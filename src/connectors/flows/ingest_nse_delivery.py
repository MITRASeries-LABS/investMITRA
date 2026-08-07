"""investMITRA — NSE Delivery Flow (GitHub Actions + Prefect)"""
from __future__ import annotations
import logging
import os
from datetime import date, datetime, timezone, timedelta

IST = __import__('datetime').timezone(__import__('datetime').timedelta(hours=5, minutes=30))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)
logger.info("NSE Delivery % temporarily disabled — URL format changed")
    return

def ingest_nse_delivery():
    from src.connectors.nse_delivery import NSEDeliveryConnector
    from src.transforms.lake_writer import write_to_lake
    from src.quality.db_logger import log_pipeline_run

    date_str = os.getenv("TRADE_DATE", "")
    target_date = date.fromisoformat(date_str) if date_str else datetime.now(IST).date()

    if target_date.weekday() >= 5:
        logger.info("Weekend — no NSE delivery data. Skipping.")
        return

    logger.info("NSE Delivery ingestion for %s", target_date)
    connector = NSEDeliveryConnector()
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    try:
        df, result = connector.ingest(target_date)
    except Exception as e:
        if "404" in str(e):
            logger.info("404 — market holiday. Skipping.")
            return
        raise

    if df.empty:
        logger.warning("No delivery data")
        return

    write_to_lake(df=df, domain="market_data", table="nse_delivery",
                  partition_date=target_date, source_id="nse_delivery",
                  quality_score=result.quality_score, run_id=run_id)
    log_pipeline_run(source_id="nse_delivery", run_date=target_date,
                     status="success", rows_ingested=len(df), quality_score=result.quality_score)
    logger.info("NSE Delivery done — rows=%d", len(df))


if __name__ == "__main__":
    ingest_nse_delivery()
