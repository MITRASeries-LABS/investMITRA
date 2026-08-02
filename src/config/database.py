"""
ClearedCircle — Database Configuration

Local dev  → Docker postgres:15 (port 5432) + timescaledb (port 5433)
Production → Supabase free tier (Postgres 15, 500MB)

Supabase free tier limitations we've designed around:
  - No TimescaleDB extension → equity_prices uses declarative partitioning
  - 500MB storage → fine for metadata; raw data goes to Cloudflare R2
  - 2 projects max → use one project, separate schemas if needed
  - Connection limit: 60 → always use connection pooling (Supabase has built-in pooler)
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator

import psycopg2
import psycopg2.pool


# ---------------------------------------------------------------------------
# Connection URLs
# ---------------------------------------------------------------------------

def get_postgres_url() -> str:
    """
    Metadata DB (company_master, source_registry, etc.)
    Local: Docker postgres on port 5432
    Prod:  Supabase connection string (use the pooler URL, not direct)
    """
    return os.getenv(
        "CC_POSTGRES_URL",
        "postgresql://cc:cc@localhost:5432/clearedcircle"
    )


def get_timescale_url() -> str:
    """
    Time-series DB (equity_prices hypertable)
    Local: Docker timescaledb on port 5433
    Prod:  Same as CC_POSTGRES_URL (plain partitioned table on Supabase)
    """
    return os.getenv(
        "CC_TIMESCALE_URL",
        "postgresql://cc:cc@localhost:5433/clearedcircle"
    )


def get_schema() -> str:
    """Schema name. investmitra on Supabase, public locally."""
    return os.getenv("CC_DB_SCHEMA", "public")


# ---------------------------------------------------------------------------
# Connection pool (shared across the process)
# ---------------------------------------------------------------------------

_pg_pool: psycopg2.pool.ThreadedConnectionPool | None = None
_ts_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool(url: str, attr: str) -> psycopg2.pool.ThreadedConnectionPool:
    global _pg_pool, _ts_pool
    pool = _pg_pool if attr == "_pg_pool" else _ts_pool
    if pool is None:
        pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=10,     # stay well under Supabase free tier limit of 60
            dsn=url,
            connect_timeout=10,
        )
        if attr == "_pg_pool":
            _pg_pool = pool
        else:
            _ts_pool = pool
    return pool


@contextmanager
def get_pg_conn() -> Generator[psycopg2.extensions.connection, None, None]:
    """Context manager: get a connection from the metadata DB pool."""
    pool = _get_pool(get_postgres_url(), "_pg_pool")
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


@contextmanager
def get_ts_conn() -> Generator[psycopg2.extensions.connection, None, None]:
    """Context manager: get a connection from the time-series DB pool."""
    pool = _get_pool(get_timescale_url(), "_ts_pool")
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)
