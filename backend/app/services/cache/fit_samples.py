"""L1 Redis cache for parsed FIT workout exports.

Key shape: fit:workout:{provider}:{user_id}:{workout_key}:{fields_hash}
Value: JSON-encoded export response (dict).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any
from uuid import UUID

from app.config import settings
from app.integrations.redis_client import get_redis_client
from app.utils.structured_logging import json_serial

logger = logging.getLogger(__name__)

KEY_PREFIX = "fit:workout"
FIELDS_HASH_LENGTH = 12


def cache_key(provider: str, user_id: UUID, workout_key: str, fields: tuple[str, ...]) -> str:
    return f"{KEY_PREFIX}:{provider}:{user_id}:{workout_key}:{_fields_hash(fields)}"


def get_cached_export(
    provider: str,
    user_id: UUID,
    workout_key: str,
    fields: tuple[str, ...],
) -> dict[str, Any] | None:
    try:
        raw = get_redis_client().get(cache_key(provider, user_id, workout_key, fields))
    except Exception:
        logger.exception("Failed to read FIT export from Redis cache")
        return None
    if raw is None or not isinstance(raw, (str, bytes, bytearray)):
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Cached FIT export is not valid JSON; ignoring", extra={"workout_key": workout_key})
        return None


def set_cached_export(
    provider: str,
    user_id: UUID,
    workout_key: str,
    fields: tuple[str, ...],
    payload: dict[str, Any],
) -> None:
    try:
        get_redis_client().setex(
            cache_key(provider, user_id, workout_key, fields),
            settings.fit_export_cache_ttl_seconds,
            json.dumps(payload, default=json_serial),
        )
    except Exception:
        logger.exception("Failed to write FIT export to Redis cache")


def _fields_hash(fields: tuple[str, ...]) -> str:
    normalised = ",".join(sorted(fields))
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:FIELDS_HASH_LENGTH]
