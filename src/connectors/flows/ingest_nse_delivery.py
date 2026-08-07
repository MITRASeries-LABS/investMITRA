"""investMITRA — NSE Delivery Flow (GitHub Actions + Prefect)"""
from __future__ import annotations
import logging
import os
from datetime import date, datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def ingest_nse_delivery():
    logger.info("NSE Delivery % temporarily disabled — URL format changed")
    return


if __name__ == "__main__":
    ingest_nse_delivery()