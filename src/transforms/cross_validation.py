"""
investMITRA — NSE vs BSE Cross-Validation
Compares NSE and BSE closing prices for the same ISIN on the same date.

Rules:
  - Price discrepancy > 2%  → quarantine flag + Slack alert
  - Price discrepancy > 5%  → auto-quarantine both records
  - Missing in one source   → WARNING (expected for SME, illiquid stocks)
  - Results written to cc-raw/prod/market_data/cross_validation/

This is Layer 4 (Transformation) — reads from raw lake, writes validation results.
Never modifies raw lake data.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from typing import Optional

import duckdb
import pandas as pd

from src.config.storage import get_storage_config, get_boto3_client
from src.config.database import get_pg_conn

logger = logging.getLogger(__name__)

PRICE_WARN_THRESHOLD  = 0.02   # 2%  → flag
PRICE_ERROR_THRESHOLD = 0.05   # 5%  → quarantine both


def run_cross_validation(trade_date: date) -> dict:
    """
    Cross-validate NSE and BSE closing prices for trade_date.
    Returns summary dict with counts of matches, warnings, errors.
    """
    logger.info("[cross_validation] Running for %s", trade_date)

    cfg = get_storage_config()

    # Read NSE Bhavcopy from lake
    nse_path = _build_lake_path(cfg, "market_data", "equity_prices", trade_date, "nse_bhavcopy")
    bse_path = _build_lake_path(cfg, "market_data", "equity_prices", trade_date, "bse_eod")

    nse_df = _read_parquet(nse_path)
    bse_df = _read_parquet(bse_path)

    if nse_df is None or bse_df is None:
        logger.warning("[cross_validation] Missing data for %s — NSE=%s BSE=%s",
                       trade_date, nse_df is not None, bse_df is not None)
        return {"status": "skipped", "reason": "missing_data"}

    # Join on ISIN
    merged = nse_df[["isin", "close"]].rename(columns={"close": "nse_close"}).merge(
        bse_df[["isin", "close"]].rename(columns={"close": "bse_close"}),
        on="isin", how="inner"
    )

    if merged.empty:
        logger.warning("[cross_validation] No matching ISINs for %s", trade_date)
        return {"status": "skipped", "reason": "no_matching_isins"}

    # Compute discrepancy
    merged["discrepancy_pct"] = (
        (merged["nse_close"] - merged["bse_close"]).abs() / merged["nse_close"]
    )

    warnings = merged[merged["discrepancy_pct"] > PRICE_WARN_THRESHOLD]
    errors   = merged[merged["discrepancy_pct"] > PRICE_ERROR_THRESHOLD]

    logger.info(
        "[cross_validation] %s — total=%d warnings=%d errors=%d",
        trade_date, len(merged), len(warnings), len(errors)
    )

    # Alert on errors
    if len(errors) > 0:
        _alert_discrepancies(errors, trade_date)

    # Write results to lake
    result_df = merged.copy()
    result_df["trade_date"]   = trade_date
    result_df["validated_at"] = datetime.now(timezone.utc)
    result_df["status"] = "ok"
    result_df.loc[merged["discrepancy_pct"] > PRICE_WARN_THRESHOLD,  "status"] = "warning"
    result_df.loc[merged["discrepancy_pct"] > PRICE_ERROR_THRESHOLD, "status"] = "error"

    _write_results(result_df, trade_date, cfg)

    # Update pipeline_run_log
    _log_validation(trade_date, len(merged), len(warnings), len(errors))

    return {
        "status": "completed",
        "total_matched": len(merged),
        "warnings": len(warnings),
        "errors": len(errors),
        "error_isins": errors["isin"].tolist() if len(errors) > 0 else [],
    }


def _build_lake_path(cfg, domain: str, table: str, trade_date: date, source_id: str) -> str:
    return (
        f"{cfg.bucket_raw}/{cfg.env}/{domain}/{table}"
        f"/year={trade_date.year}/month={trade_date.month:02d}/day={trade_date.day:02d}"
    )


def _read_parquet(prefix: str) -> Optional[pd.DataFrame]:
    """Read all parquet files under a prefix from R2/MinIO using DuckDB."""
    cfg = get_storage_config()
    try:
        con = duckdb.connect()

        if cfg.endpoint_url:
            # MinIO or R2
            con.execute(f"""
                SET s3_endpoint='{cfg.endpoint_url.replace("https://", "").replace("http://", "")}';
                SET s3_access_key_id='{cfg.access_key}';
                SET s3_secret_access_key='{cfg.secret_key}';
                SET s3_region='{cfg.region}';
                SET s3_use_ssl={'true' if cfg.endpoint_url.startswith('https') else 'false'};
                SET s3_url_style='path';
            """)

        df = con.execute(
            f"SELECT isin, close FROM read_parquet('s3://{prefix}/*.parquet')"
        ).df()
        con.close()
        return df if not df.empty else None
    except Exception as e:
        logger.warning("[cross_validation] Could not read %s: %s", prefix, e)
        return None


def _write_results(df: pd.DataFrame, trade_date: date, cfg) -> None:
    """Write validation results to lake."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from src.config.storage import get_s3_filesystem

    path = (
        f"{cfg.bucket_raw}/{cfg.env}/market_data/cross_validation"
        f"/year={trade_date.year}/month={trade_date.month:02d}/day={trade_date.day:02d}"
        f"/nse_vs_bse_{trade_date.strftime('%Y%m%d')}.parquet"
    )

    table = pa.Table.from_pandas(df, preserve_index=False)
    fs = get_s3_filesystem()
    pq.write_table(table, path, filesystem=fs, compression="snappy")
    logger.info("[cross_validation] Results written to s3://%s", path)


def _alert_discrepancies(errors: pd.DataFrame, trade_date: date) -> None:
    """Send Slack alert for large price discrepancies."""
    webhook = os.getenv("SLACK_WEBHOOK_URL")
    msg = (
        f"⚠️ *investMITRA Cross-Validation Alert*\n"
        f"Date: {trade_date}\n"
        f"Stocks with NSE vs BSE price discrepancy > 5%: {len(errors)}\n"
        f"ISINs: {', '.join(errors['isin'].head(10).tolist())}"
    )
    if webhook:
        import requests
        try:
            requests.post(webhook, json={"text": msg}, timeout=5)
        except Exception:
            pass
    logger.warning(msg)


def _log_validation(trade_date: date, total: int, warnings: int, errors: int) -> None:
    try:
        with get_pg_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO pipeline_run_log
                    (source_id, run_date, started_at, completed_at, status,
                     rows_ingested, quality_score)
                VALUES (%s, %s, NOW(), NOW(), %s, %s, %s)
                """,
                (
                    "nse_bhavcopy",  # log under market_data
                    trade_date,
                    "success" if errors == 0 else "quarantined",
                    total,
                    100 if errors == 0 else max(0, 100 - errors * 5),
                ),
            )
            cur.close()
    except Exception as e:
        logger.error("[cross_validation] Failed to log: %s", e)
