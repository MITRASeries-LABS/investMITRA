"""
investMITRA — Prefect Flow: NSE F&O Bhavcopy Daily Ingestion
Flow name: ingest_market_data_nse_fo_bhavcopy_daily
Schedule: 8:45 PM IST (15:15 UTC) Mon-Fri
"""

from __future__ import annotations

from datetime import date, datetime

from prefect import flow, task, get_run_logger

from src.connectors.nse_fo_bhavcopy import NSEFOBhavCopyConnector
from src.transforms.lake_writer import write_to_lake
from src.quality.db_logger import log_pipeline_run


@task(name="fetch_nse_fo_bhavcopy", retries=3, retry_delay_seconds=900)
def fetch_task(target_date: date):
    log = get_run_logger()
    connector = NSEFOBhavCopyConnector()
    log.info("Fetching NSE F&O Bhavcopy for %s", target_date)
    df, result = connector.ingest(target_date)
    log.info("rows=%d quality=%d instruments=%s",
             len(df), result.quality_score,
             df["instrument_type"].value_counts().to_dict() if "instrument_type" in df.columns else {})
    return df, result


@task(name="write_fo_to_lake")
def write_task(df_and_result, target_date: date, run_id: str) -> dict:
    df, result = df_and_result
    path = write_to_lake(
        df=df,
        domain="market_data",
        table="fo_bhavcopy",
        partition_date=target_date,
        source_id="nse_fo_bhavcopy",
        quality_score=result.quality_score,
        run_id=run_id,
    )
    destination = "quarantine" if result.quality_score < 50 else "raw"
    return {"path": path, "destination": destination, "rows": len(df), "quality_score": result.quality_score}


@task(name="log_fo_run")
def log_task(write_result: dict, target_date: date, run_id: str):
    log_pipeline_run(
        source_id="nse_fo_bhavcopy",
        run_date=target_date,
        status="quarantined" if write_result["destination"] == "quarantine" else "success",
        rows_ingested=write_result["rows"],
        quality_score=write_result["quality_score"],
        prefect_run_id=run_id,
    )


@flow(name="ingest_market_data_nse_fo_bhavcopy_daily")
def ingest_nse_fo_bhavcopy():
    target_date = date.today()
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    result = fetch_task(target_date)
    write_result = write_task(result, target_date, run_id)
    log_task(write_result, target_date, run_id)
    return write_result


if __name__ == "__main__":
    ingest_nse_fo_bhavcopy()
