"""investMITRA — EOD Processing Flow (GitHub Actions + Prefect)"""
from __future__ import annotations
import logging
import os
from datetime import date, datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def eod_processing_daily():
    from src.connectors.nse_circuit_limits import NSECircuitLimitsConnector
    from src.transforms.lake_writer import write_to_lake
    from src.transforms.cross_validation import run_cross_validation
    from src.transforms.corporate_actions import run_adjustment_for_all
    from src.quality.db_logger import log_pipeline_run

    date_str = os.getenv("TRADE_DATE", "")
    target_date = date.fromisoformat(date_str) if date_str else datetime.now(IST).date()
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    if target_date.weekday() >= 5:
        logger.info("Weekend — skipping EOD processing.")
        return

    logger.info("EOD Processing for %s", target_date)

    # Step 1: Circuit limits
    try:
        connector = NSECircuitLimitsConnector()
        df, result = connector.ingest(target_date)
        if not df.empty and result.quality_score >= 50:
            write_to_lake(df=df, domain="market_data", table="circuit_limits",
                          partition_date=target_date, source_id="nse_circuit_limits",
                          quality_score=result.quality_score, run_id=run_id)
            log_pipeline_run(source_id="nse_circuit_limits", run_date=target_date,
                             status="success", rows_ingested=len(df),
                             quality_score=result.quality_score)
        logger.info("Circuit limits done — rows=%d", len(df))
    except Exception as e:
        if "404" in str(e):
            logger.info("Circuit limits — market holiday. Skipping.")
        else:
            logger.error("Circuit limits failed: %s", e)

    # Step 2: Cross-validation
    try:
        result = run_cross_validation(target_date)
        logger.info("Cross-validation done: %s", result)
    except Exception as e:
        logger.error("Cross-validation failed: %s", e)

    # Step 3: CA adjustment
    try:
        nse = run_adjustment_for_all(source="NSE")
        bse = run_adjustment_for_all(source="BSE")
        logger.info("CA adjustment done — NSE: %s BSE: %s", nse, bse)
    except Exception as e:
        logger.error("CA adjustment failed: %s", e)

    logger.info("EOD Processing complete for %s", target_date)


if __name__ == "__main__":
    eod_processing_daily()
