"""
ClearedCircle — Lake Writer
Routes DataFrames to raw or quarantine on object storage.

Local:  MinIO (docker-compose)
Prod:   Cloudflare R2 (free tier, S3-compatible)

Partition layout:
  {bucket}/{env}/{prefix}/{domain}/{table}/year={Y}/month={M}/day={D}/{source}_{ts}.parquet

Rules:
  quality_score >= 50  → cc-raw bucket
  quality_score <  50  → cc-quarantine bucket
  Raw lake is IMMUTABLE — never overwrite, never delete.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src.config.storage import get_storage_config, get_s3_filesystem

logger = logging.getLogger(__name__)


def write_to_lake(
    df: pd.DataFrame,
    domain: str,
    table: str,
    partition_date: date,
    source_id: str,
    quality_score: int,
    run_id: Optional[str] = None,
) -> str:
    """
    Write a DataFrame to the lake. Returns the full s3:// path.

    Routes:
      quality_score >= 50 → cc-raw/{env}/...
      quality_score <  50 → cc-quarantine/{env}/...
    """
    cfg = get_storage_config()
    bucket = cfg.bucket_raw if quality_score >= 50 else cfg.bucket_quarantine
    env = cfg.env

    path = (
        f"{bucket}/{env}/{domain}/{table}"
        f"/year={partition_date.year}"
        f"/month={partition_date.month:02d}"
        f"/day={partition_date.day:02d}"
        f"/{source_id}_{run_id or _ts()}.parquet"
    )

    logger.info(
        "[lake_writer] %d rows → s3://%s  (quality=%d, env=%s)",
        len(df), path, quality_score, env
    )

    table_pa = pa.Table.from_pandas(df, preserve_index=False)
    fs = get_s3_filesystem()

    pq.write_table(
        table_pa,
        path,
        filesystem=fs,
        compression="snappy",
        write_statistics=True,
    )

    full_path = f"s3://{path}"
    logger.info("[lake_writer] Written → %s", full_path)
    return full_path


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
