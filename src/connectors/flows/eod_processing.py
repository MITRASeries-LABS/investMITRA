"""investMITRA — EOD Processing Flow (GitHub Actions + Prefect)"""
from __future__ import annotations
import logging
import os
from datetime import date, datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def eod_processing_daily():
    from src.transforms.cross_validation import run_cross_validation
    from src.transforms.corporate_actions import run_adjustment_for_all

    date_str = os.getenv("TRADE_DATE", "")
    target_date = date.fromisoformat(date_str) if date_str else datetime.now(IST).date()

    if target_date.weekday() >= 5:
        logger.info("Weekend — skipping EOD processing.")
        return

    logger.info("EOD Processing for %s", target_date)

    # Step 1: Circuit limits — temporarily disabled (NSE removed MTO files)
    logger.info("Circuit limits temporarily disabled")

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