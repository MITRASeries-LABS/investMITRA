"""
ClearedCircle — Storage Configuration
Single source of truth for object store settings.

Local dev  → MinIO (docker-compose)
Production → Cloudflare R2 (free: 10GB, 10M reads/month, zero egress cost)

Zero code changes needed to switch — only env vars differ.

Cloudflare R2 is S3-compatible. The only difference from AWS S3:
  - endpoint_url = https://<ACCOUNT_ID>.r2.cloudflarestorage.com
  - No region needed (R2 is global)
  - No egress fees (unlike S3)
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class StorageConfig:
    endpoint_url: str | None    # None = real AWS S3
    access_key: str
    secret_key: str
    bucket_raw: str
    bucket_quarantine: str
    region: str
    env: str

    @property
    def is_local(self) -> bool:
        return self.env == "local"

    @property
    def is_r2(self) -> bool:
        return self.endpoint_url is not None and "r2.cloudflarestorage.com" in (self.endpoint_url or "")


def get_storage_config() -> StorageConfig:
    """
    Load storage config from environment variables.

    Local dev (.env):
        CC_ENV=local
        AWS_ENDPOINT_URL=http://localhost:9000
        AWS_ACCESS_KEY_ID=minioadmin
        AWS_SECRET_ACCESS_KEY=minioadmin

    Production (Cloudflare R2):
        CC_ENV=prod
        AWS_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
        AWS_ACCESS_KEY_ID=<R2_ACCESS_KEY>
        AWS_SECRET_ACCESS_KEY=<R2_SECRET_KEY>
        CC_BUCKET_RAW=cc-raw
        CC_BUCKET_QUARANTINE=cc-quarantine
    """
    return StorageConfig(
        endpoint_url=os.getenv("AWS_ENDPOINT_URL"),       # None in prod AWS; set for MinIO/R2
        access_key=os.getenv("AWS_ACCESS_KEY_ID", ""),
        secret_key=os.getenv("AWS_SECRET_ACCESS_KEY", ""),
        bucket_raw=os.getenv("CC_BUCKET_RAW", "cc-raw"),
        bucket_quarantine=os.getenv("CC_BUCKET_QUARANTINE", "cc-quarantine"),
        region=os.getenv("AWS_REGION", "auto"),           # R2 uses "auto"
        env=os.getenv("CC_ENV", "local"),
    )


def get_s3_filesystem():
    """
    Return a PyArrow S3FileSystem for the configured storage backend.
    Works with MinIO (local), Cloudflare R2 (prod), or real AWS S3.
    """
    import pyarrow.fs as pafs
    cfg = get_storage_config()

    if cfg.endpoint_url:
        from urllib.parse import urlparse
        parsed = urlparse(cfg.endpoint_url)
        return pafs.S3FileSystem(
            access_key=cfg.access_key,
            secret_key=cfg.secret_key,
            endpoint_override=f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname,
            scheme=parsed.scheme,
            region=cfg.region,
        )
    else:
        return pafs.S3FileSystem(region=cfg.region)


def get_boto3_client():
    """
    Return a boto3 S3 client. Use for operations PyArrow doesn't cover
    (e.g. listing, deleting, presigned URLs).
    """
    import boto3
    cfg = get_storage_config()

    kwargs = dict(
        aws_access_key_id=cfg.access_key,
        aws_secret_access_key=cfg.secret_key,
        region_name=cfg.region,
    )
    if cfg.endpoint_url:
        kwargs["endpoint_url"] = cfg.endpoint_url

    return boto3.client("s3", **kwargs)
