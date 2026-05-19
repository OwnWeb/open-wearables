"""L2 S3 storage for raw FIT bytes.

Feature-flagged via settings.persist_raw_fit. Disabled by default.
Layout: {prefix}/{provider}/{user_id}/{workout_key}.fit.gz
"""

from __future__ import annotations

import gzip
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

_enabled: bool = False
_s3_bucket: str | None = None
_s3_prefix: str = "raw-fit"
_s3_client: Any = None


def configure(
    enabled: bool,
    s3_bucket: str | None,
    s3_prefix: str = "raw-fit",
    s3_endpoint_url: str | None = None,
) -> None:
    """Called once at startup from settings."""
    global _enabled, _s3_bucket, _s3_prefix, _s3_client
    _enabled = False
    _s3_prefix = s3_prefix

    if not enabled:
        return

    if not s3_bucket:
        logger.error("persist_raw_fit=true but no S3 bucket configured")
        return

    client = _create_s3_client(endpoint_url=s3_endpoint_url)
    if client is None:
        logger.error("Failed to create S3 client for raw FIT storage; staying disabled")
        return

    _s3_bucket = s3_bucket
    _s3_client = client
    _enabled = True


def is_enabled() -> bool:
    return _enabled


def get_fit_bytes(provider: str, user_id: UUID, workout_key: str) -> bytes | None:
    if not _enabled or _s3_client is None or _s3_bucket is None:
        return None
    key = _object_key(provider, user_id, workout_key)
    try:
        response = _s3_client.get_object(Bucket=_s3_bucket, Key=key)
        compressed = response["Body"].read()
        return gzip.decompress(compressed)
    except _s3_client.exceptions.NoSuchKey:
        return None
    except Exception:
        logger.exception("Failed to read FIT from S3", extra={"bucket": _s3_bucket, "key": key})
        return None


def put_fit_bytes(
    provider: str,
    user_id: UUID,
    workout_key: str,
    fit_bytes: bytes,
    *,
    sha256_hex: str | None = None,
) -> None:
    if not _enabled or _s3_client is None or _s3_bucket is None:
        return
    key = _object_key(provider, user_id, workout_key)
    metadata: dict[str, str] = {
        "provider": provider,
        "user_id": str(user_id),
        "workout_key": workout_key,
        "fetched_at": datetime.now(UTC).isoformat(),
        "size_bytes": str(len(fit_bytes)),
    }
    if sha256_hex:
        metadata["sha256"] = sha256_hex

    try:
        _s3_client.put_object(
            Bucket=_s3_bucket,
            Key=key,
            Body=gzip.compress(fit_bytes),
            ContentType="application/vnd.ant.fit",
            ContentEncoding="gzip",
            Metadata=metadata,
        )
    except Exception:
        logger.exception("Failed to write FIT to S3", extra={"bucket": _s3_bucket, "key": key})


def delete_for_user(user_id: UUID) -> int:
    """Purge every FIT object owned by a user across all providers. Returns count deleted."""
    if not _enabled or _s3_client is None or _s3_bucket is None:
        return 0
    deleted = 0
    for provider_prefix in _list_provider_prefixes():
        deleted += _delete_prefix(f"{provider_prefix}{user_id}/")
    return deleted


def _list_provider_prefixes() -> list[str]:
    paginator = _s3_client.get_paginator("list_objects_v2")
    prefixes: list[str] = []
    for page in paginator.paginate(Bucket=_s3_bucket, Prefix=f"{_s3_prefix}/", Delimiter="/"):
        for entry in page.get("CommonPrefixes", []) or []:
            prefix = entry.get("Prefix")
            if prefix:
                prefixes.append(prefix)
    return prefixes


def _delete_prefix(prefix: str) -> int:
    deleted = 0
    paginator = _s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=_s3_bucket, Prefix=prefix):
        objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
        if not objects:
            continue
        _s3_client.delete_objects(Bucket=_s3_bucket, Delete={"Objects": objects})
        deleted += len(objects)
    return deleted


def _object_key(provider: str, user_id: UUID, workout_key: str) -> str:
    return f"{_s3_prefix}/{provider}/{user_id}/{workout_key}.fit.gz"


def _create_s3_client(endpoint_url: str | None = None) -> Any:
    try:
        import boto3
        from botocore.exceptions import NoCredentialsError

        from app.config import settings

        kwargs: dict[str, Any] = {"region_name": settings.aws_region}
        if settings.aws_access_key_id and settings.aws_secret_access_key:
            kwargs["aws_access_key_id"] = settings.aws_access_key_id
            kwargs["aws_secret_access_key"] = settings.aws_secret_access_key.get_secret_value()
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url

        return boto3.client("s3", **kwargs)
    except (NoCredentialsError, AttributeError, Exception) as e:
        logger.error("Cannot create S3 client for raw FIT storage: %s", e)
        return None
