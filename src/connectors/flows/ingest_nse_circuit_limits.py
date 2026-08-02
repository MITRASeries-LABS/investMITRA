"""
investMITRA — Prefect Flow: NSE Circuit Limits Daily Ingestion
Schedule: 8:00 PM IST (14:30 UTC) Mon-Fri — same time as Bhavcopy
"""

from __future__ import annotations

from datetime import date, datetime

from prefect import flow, task, get_run_logger

from src.connectors.nse_circuit_limits import NSECircuitLimitsConnector
from src.transforms.lake_writer import write_to_lake
from src.quality.db_logger import log_pipeline_run


@task(name="fetch_nse_circuit_limits", retries=3, retry_delay_seconds=900)
def fetch_task(target_date: date):
    log = get_run_logger()
    connector = NSECircuitLimitsConnector()
    log.info("Fetching NSE Circuit Limits for %s", target_date)
    df, result = connector.ingest(target_date)
    log.info("rows=%d quality=%d", len(df), result.quality_score)
    return df, result


@task(name="write_circuit_to_lake")
def write_task(df_and_result, target_date: date, run_id: str) -> dict:
    df, result = df_and_result
    path = write_to_lake(
        df=df,
        domain="market_data",
        table="circuit_limits",
        partition_date=target_date,
        source_id="nse_circuit_limits",
        quality_score=result.quality_score,
        run_id=run_id,
    )
    destination = "quarantine" if result.quality_score < 50 else "raw"
    return {"path": path, "destination": destination, "rows": len(df), "quality_score": result.quality_score}


@task(name="log_circuit_run")
def log_task(write_result: dict, target_date: date, run_id: str):
    log_pipeline_run(
        source_id="nse_circuit_limits",
        run_date=target_date,
        status="quarantined" if write_result["destination"] == "quarantine" else "success",
        rows_ingested=write_result["rows"],
        quality_score=write_result["quality_score"],
        prefect_run_id=run_id,
    )


@flow(name="ingest_market_data_nse_circuit_limits_daily")
def ingest_nse_circuit_limits():
    target_date = date.today()
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    result = fetch_task(target_date)
    write_result = write_task(result, target_date, run_id)
    log_task(write_result, target_date, run_id)
    return write_result


if __name__ == "__main__":
    ingest_nse_circuit_limits()
