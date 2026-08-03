"""
investMITRA — Macroeconomic Flows
Runs via GitHub Actions (macroeconomic.yml)
"""

from __future__ import annotations
import logging
import os
from datetime import date, datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _write_macro_to_db(df):
    """Write macro indicators to Neon macroeconomic_indicators table."""
    from src.config.database import get_pg_conn
    with get_pg_conn() as conn:
        cur = conn.cursor()
        written = 0
        for _, row in df.iterrows():
            try:
                cur.execute(
                    """
                    INSERT INTO investmitra.macroeconomic_indicators
                        (indicator_id, source_id, observation_date,
                         value, unit, data_vintage, is_revised)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (indicator_id, observation_date, data_vintage)
                    DO NOTHING
                    """,
                    (
                        row.get("indicator_id"),
                        row.get("source_id"),
                        row.get("observation_date"),
                        row.get("value"),
                        row.get("unit"),
                        row.get("data_vintage", datetime.utcnow().isoformat()),
                        row.get("is_revised", False),
                    ),
                )
                written += 1
            except Exception as e:
                logger.warning("DB write failed: %s", e)
        cur.close()
        logger.info("Wrote %d macro rows to DB", written)


def ingest_rbi_dbie():
    from src.connectors.rbi_dbie import RBIDBIEConnector
    from src.transforms.lake_writer import write_to_lake
    from src.quality.db_logger import log_pipeline_run

    logger.info("RBI DBIE ingestion started")
    connector = RBIDBIEConnector()
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    target_date = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()

    df, result = connector.ingest(target_date)
    if df.empty:
        logger.warning("No RBI DBIE data")
        return

    write_to_lake(df=df, domain="macroeconomic", table="rbi_indicators",
                  partition_date=target_date, source_id="rbi_dbie",
                  quality_score=result.quality_score, run_id=run_id)
    _write_macro_to_db(df)
    log_pipeline_run(source_id="rbi_dbie", run_date=target_date,
                     status="success", rows_ingested=len(df),
                     quality_score=result.quality_score)
    logger.info("RBI DBIE done — rows=%d", len(df))


def ingest_mospi():
    from src.connectors.mospi import MOSPIConnector
    from src.transforms.lake_writer import write_to_lake
    from src.quality.db_logger import log_pipeline_run

    logger.info("MOSPI ingestion started")
    connector = MOSPIConnector()
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    target_date = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()

    df, result = connector.ingest(target_date)
    if df.empty:
        logger.warning("No MOSPI data")
        return

    write_to_lake(df=df, domain="macroeconomic", table="mospi_indicators",
                  partition_date=target_date, source_id="mospi",
                  quality_score=result.quality_score, run_id=run_id)
    _write_macro_to_db(df)
    log_pipeline_run(source_id="mospi", run_date=target_date,
                     status="success", rows_ingested=len(df),
                     quality_score=result.quality_score)
    logger.info("MOSPI done — rows=%d", len(df))


def ingest_fred():
    from src.connectors.fred_api import FREDConnector
    from src.transforms.lake_writer import write_to_lake
    from src.quality.db_logger import log_pipeline_run

    logger.info("FRED API ingestion started")
    connector = FREDConnector()
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    target_date = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()

    df, result = connector.ingest(target_date)
    if df.empty:
        logger.warning("No FRED data — check FRED_API_KEY")
        return

    write_to_lake(df=df, domain="macroeconomic", table="fred_indicators",
                  partition_date=target_date, source_id="fred_api",
                  quality_score=result.quality_score, run_id=run_id)
    _write_macro_to_db(df)
    log_pipeline_run(source_id="fred_api", run_date=target_date,
                     status="success", rows_ingested=len(df),
                     quality_score=result.quality_score)
    logger.info("FRED done — rows=%d", len(df))


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    if target in ("all", "rbi"):    ingest_rbi_dbie()
    if target in ("all", "mospi"):  ingest_mospi()
    if target in ("all", "fred"):   ingest_fred()
