"""investMITRA — NSE Bhavcopy Flow (GitHub Actions + Prefect)"""
from __future__ import annotations
import logging
import os
from datetime import date, datetime, timezone, timedelta

IST = __import__('datetime').timezone(__import__('datetime').timedelta(hours=5, minutes=30))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def ingest_nse_bhavcopy(target_date: date = None):
    from src.connectors.nse_bhavcopy import NSEBhavCopyConnector
    from src.transforms.lake_writer import write_to_lake
    from src.quality.db_logger import log_pipeline_run

    if target_date is None:
        date_str = os.getenv("TRADE_DATE", "")
        target_date = date.fromisoformat(date_str) if date_str else datetime.now(IST).date()

    if target_date.weekday() >= 5:
        logger.info("Weekend — no NSE data for %s. Skipping.", target_date)
        return

    logger.info("NSE Bhavcopy ingestion for %s", target_date)
    connector = NSEBhavCopyConnector()
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    try:
        df, result = connector.ingest(target_date)
    except Exception as e:
        if "404" in str(e) or "No data" in str(e):
            logger.info("404 — market holiday on %s. Skipping.", target_date)
            return
        raise

    if df.empty:
        logger.warning("No data returned")
        return

    path = write_to_lake(df=df, domain="market_data", table="equity_prices",
                         partition_date=target_date, source_id="nse_bhavcopy",
                         quality_score=result.quality_score, run_id=run_id)
    log_pipeline_run(source_id="nse_bhavcopy", run_date=target_date,
                     status="success" if result.quality_score >= 50 else "quarantined",
                     rows_ingested=len(df), quality_score=result.quality_score)
    logger.info("NSE Bhavcopy done — rows=%d quality=%d", len(df), result.quality_score)


if __name__ == "__main__":
    ingest_nse_bhavcopy()
