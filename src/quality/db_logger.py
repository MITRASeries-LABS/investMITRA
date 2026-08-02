"""
ClearedCircle — Pipeline Run Logger
Writes to pipeline_run_log and updates source_registry in Supabase/Postgres.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from src.config.database import get_pg_conn

logger = logging.getLogger(__name__)


def log_pipeline_run(
    source_id: str,
    run_date: date,
    status: str,
    rows_ingested: int = 0,
    rows_quarantined: int = 0,
    quality_score: Optional[int] = None,
    prefect_run_id: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    """Insert run into pipeline_run_log and update source_registry."""
    try:
        with get_pg_conn() as conn:
            cur = conn.cursor()

            cur.execute(
                """
                INSERT INTO pipeline_run_log
                    (source_id, run_date, started_at, completed_at, status,
                     rows_ingested, rows_quarantined, quality_score,
                     error_message, prefect_run_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    source_id, run_date,
                    datetime.now(timezone.utc), datetime.now(timezone.utc),
                    status, rows_ingested, rows_quarantined,
                    quality_score, error_message, prefect_run_id,
                ),
            )

            if status == "success" and quality_score is not None:
                cur.execute(
                    """
                    UPDATE source_registry SET
                        last_successful_run  = NOW(),
                        last_quality_score   = %s,
                        consecutive_failures = 0,
                        updated_at           = NOW()
                    WHERE source_id = %s
                    """,
                    (quality_score, source_id),
                )
            elif status == "failed":
                cur.execute(
                    """
                    UPDATE source_registry SET
                        consecutive_failures = consecutive_failures + 1,
                        updated_at = NOW()
                    WHERE source_id = %s
                    """,
                    (source_id,),
                )

            # Auto-disable source if 30d avg quality < 60
            cur.execute(
                """
                WITH avg AS (
                    SELECT AVG(quality_score) AS q
                    FROM pipeline_run_log
                    WHERE source_id = %s
                      AND run_date >= CURRENT_DATE - INTERVAL '30 days'
                      AND quality_score IS NOT NULL
                )
                UPDATE source_registry
                SET
                    avg_quality_score_30d = avg.q,
                    is_active        = CASE WHEN avg.q < 60 THEN FALSE ELSE is_active END,
                    auto_disabled_at = CASE WHEN avg.q < 60 THEN NOW() ELSE auto_disabled_at END,
                    updated_at       = NOW()
                FROM avg
                WHERE source_registry.source_id = %s
                """,
                (source_id, source_id),
            )

            cur.close()

    except Exception as e:
        # Never let logging failure crash the pipeline
        logger.error("[db_logger] Failed for %s: %s", source_id, e)
