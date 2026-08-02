"""
investMITRA — Prefect Flow: Corporate Action Adjustment
Schedule: 9:30 PM IST (16:00 UTC) Mon-Fri
  Runs after all price ingestion and cross-validation are complete.

Also run manually after adding new corporate actions:
  prefect deployment run 'corporate_action_adjustment/ca-adjustment-daily'
"""

from __future__ import annotations

from datetime import date

from prefect import flow, task, get_run_logger

from src.transforms.corporate_actions import run_adjustment_for_all


@task(name="run_ca_adjustment", retries=1, retry_delay_seconds=300)
def adjustment_task():
    log = get_run_logger()
    log.info("Running corporate action adjustment for all ISINs")

    # Run for both NSE and BSE
    nse_result = run_adjustment_for_all(source="NSE")
    bse_result = run_adjustment_for_all(source="BSE")

    log.info("NSE: %s", nse_result)
    log.info("BSE: %s", bse_result)

    if nse_result["suspicious"] > 0 or bse_result["suspicious"] > 0:
        log.warning(
            "Suspicious gaps detected — NSE: %d, BSE: %d. "
            "Review corporate_actions table for missing events.",
            nse_result["suspicious"], bse_result["suspicious"]
        )

    return {"nse": nse_result, "bse": bse_result}


@flow(name="corporate_action_adjustment")
def ca_adjustment_daily():
    return adjustment_task()


if __name__ == "__main__":
    ca_adjustment_daily()
