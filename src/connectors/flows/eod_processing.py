"""
investMITRA ? Prefect Flow: EOD Processing Pipeline
Single deployment that runs all post-ingestion transforms in sequence:
  1. NSE Circuit Limits ingestion
  2. NSE vs BSE Cross-Validation
  3. Corporate Action Adjustment

Schedule: 9:30 PM IST (15:30 UTC) Mon-Fri
"""

from __future__ import annotations
from datetime import date, datetime
from prefect import flow, task, get_run_logger
from src.connectors.nse_circuit_limits import NSECircuitLimitsConnector
from src.transforms.lake_writer import write_to_lake
from src.transforms.cross_validation import run_cross_validation
from src.transforms.corporate_actions import run_adjustment_for_all
from src.quality.db_logger import log_pipeline_run

@task(name="fetch_circuit_limits", retries=3, retry_delay_seconds=600)
def circuit_limits_task(target_date: date):
    log = get_run_logger()
    log.info("Step 1/3 - Fetching NSE Circuit Limits for %s", target_date)
    connector = NSECircuitLimitsConnector()
    df, result = connector.ingest(target_date)
    log.info("Circuit limits: rows=%d quality=%d", len(df), result.quality_score)
    if result.quality_score >= 50:
        run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        write_to_lake(df=df, domain="market_data", table="circuit_limits",
            partition_date=target_date, source_id="nse_circuit_limits",
            quality_score=result.quality_score, run_id=run_id)
        log_pipeline_run(source_id="nse_circuit_limits", run_date=target_date,
            status="success", rows_ingested=len(df), quality_score=result.quality_score)
    return {"rows": len(df), "quality_score": result.quality_score}

@task(name="cross_validate", retries=2, retry_delay_seconds=300)
def cross_validation_task(target_date: date):
    log = get_run_logger()
    log.info("Step 2/3 - Cross-validating NSE vs BSE for %s", target_date)
    result = run_cross_validation(target_date)
    log.info("Cross-validation result: %s", result)
    return result

@task(name="ca_adjustment", retries=1, retry_delay_seconds=300)
def ca_adjustment_task():
    log = get_run_logger()
    log.info("Step 3/3 - Running corporate action price adjustment")
    nse_result = run_adjustment_for_all(source="NSE")
    bse_result = run_adjustment_for_all(source="BSE")
    log.info("CA Adjustment - NSE: %s", nse_result)
    log.info("CA Adjustment - BSE: %s", bse_result)
    return {"nse": nse_result, "bse": bse_result}

@flow(name="eod_processing_daily")
def eod_processing_daily():
    target_date = date.today()
    log = get_run_logger()
    log.info("EOD Processing started for %s", target_date)
    circuit_result = circuit_limits_task(target_date)
    validation_result = cross_validation_task(target_date)
    adjustment_result = ca_adjustment_task()
    log.info("EOD Processing complete for %s", target_date)
    return {"date": str(target_date), "circuit_limits": circuit_result,
            "cross_validation": validation_result, "ca_adjustment": adjustment_result}

if __name__ == "__main__":
    eod_processing_daily()
