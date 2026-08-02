"""
investMITRA — Prefect Flow: NSE vs BSE Cross-Validation
Schedule: 9:00 PM IST (15:30 UTC) Mon-Fri
  Runs after both NSE (8 PM) and BSE (8:30 PM) are complete.
"""

from __future__ import annotations

from datetime import date, datetime

from prefect import flow, task, get_run_logger

from src.transforms.cross_validation import run_cross_validation


@task(name="cross_validate_prices", retries=2, retry_delay_seconds=300)
def validate_task(target_date: date):
    log = get_run_logger()
    log.info("Cross-validating NSE vs BSE for %s", target_date)
    result = run_cross_validation(target_date)
    log.info("Result: %s", result)

    if result.get("errors", 0) > 0:
        log.warning(
            "%d stocks with >5%% NSE vs BSE discrepancy: %s",
            result["errors"],
            result.get("error_isins", [])[:10]
        )
    return result


@flow(name="cross_validate_nse_bse_daily")
def cross_validate_nse_bse():
    target_date = date.today()
    return validate_task(target_date)


if __name__ == "__main__":
    cross_validate_nse_bse()
