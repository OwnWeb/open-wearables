"""On-demand FIT-derived workout export endpoint.

Three sibling routes share a fetch pipeline (L1 Redis -> L2 S3 -> L3 provider).
JSON + CSV reuse the parsed in-memory ParsedFit; .fit streams raw bytes.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Annotated, Any, Callable, cast
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse

from app.api.serialisation.csv import stream_csv_rows
from app.config import settings
from app.database import DbSession
from app.schemas.api.workouts.export import WorkoutExportResponse
from app.schemas.enums import ProviderName
from app.services import ApiKeyDep
from app.services.cache import fit_samples as fit_cache
from app.services.event_record_service import event_record_service
from app.services.fit import (
    CANONICAL_FIELDS,
    DEFAULT_FIELDS,
    LATLNG_FIELD,
    ParsedFit,
    parse_fit,
    parsed_fit_to_csv_rows,
    parsed_fit_to_export_response,
)
from app.services.providers.factory import ProviderFactory
from app.services.storage import raw_fit as raw_fit_storage

logger = logging.getLogger(__name__)

router = APIRouter()
factory = ProviderFactory()

ALLOWED_FIELDS: frozenset[str] = frozenset({*CANONICAL_FIELDS.keys(), LATLNG_FIELD})
FIT_CONTENT_TYPE = "application/vnd.ant.fit"
PROVIDERS_WITH_FIT: frozenset[str] = frozenset({ProviderName.SUUNTO.value, ProviderName.POLAR.value})


@router.get(
    "/users/{user_id}/workouts/{workout_key}/export",
    response_model=WorkoutExportResponse,
)
def export_workout_json(
    user_id: UUID,
    workout_key: str,
    db: DbSession,
    _api_key: ApiKeyDep,
    fields: Annotated[
        str | None,
        Query(description="Comma-separated canonical field names to extract from FIT records"),
    ] = None,
) -> dict[str, Any]:
    requested_fields = _parse_fields(fields)
    provider = _resolve_provider(db, user_id, workout_key)

    cached = fit_cache.get_cached_export(provider, user_id, workout_key, requested_fields)
    if cached is not None:
        return cached

    parsed, _ = _fetch_and_parse(db, provider, user_id, workout_key, requested_fields)
    payload = parsed_fit_to_export_response(
        parsed,
        workout_key=workout_key,
        provider=provider,
        user_id=user_id,
        requested_fields=requested_fields,
    )
    fit_cache.set_cached_export(provider, user_id, workout_key, requested_fields, payload)
    return payload


@router.get("/users/{user_id}/workouts/{workout_key}/export.csv")
def export_workout_csv(
    user_id: UUID,
    workout_key: str,
    db: DbSession,
    _api_key: ApiKeyDep,
    fields: Annotated[str | None, Query()] = None,
) -> StreamingResponse:
    requested_fields = _parse_fields(fields)
    provider = _resolve_provider(db, user_id, workout_key)
    parsed, _ = _fetch_and_parse(db, provider, user_id, workout_key, requested_fields)

    return StreamingResponse(
        stream_csv_rows(parsed_fit_to_csv_rows(parsed, requested_fields)),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{workout_key}.csv"'},
    )


@router.get("/users/{user_id}/workouts/{workout_key}/export.fit")
def export_workout_fit(
    user_id: UUID,
    workout_key: str,
    db: DbSession,
    _api_key: ApiKeyDep,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Response:
    provider = _resolve_provider(db, user_id, workout_key)
    fit_bytes = _fetch_fit_bytes(db, provider, user_id, workout_key)
    etag = f'"{hashlib.sha256(fit_bytes).hexdigest()}"'

    if if_none_match and if_none_match == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})

    return Response(
        content=fit_bytes,
        media_type=FIT_CONTENT_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{workout_key}.fit"',
            "ETag": etag,
        },
    )


def _parse_fields(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return DEFAULT_FIELDS
    parsed: list[str] = []
    seen: set[str] = set()
    for chunk in raw.split(","):
        name = chunk.strip()
        if not name or name in seen:
            continue
        if name not in ALLOWED_FIELDS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown field '{name}'. Allowed: {sorted(ALLOWED_FIELDS)}",
            )
        parsed.append(name)
        seen.add(name)
        if len(parsed) > settings.fit_export_max_fields:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Too many fields requested (max {settings.fit_export_max_fields})",
            )
    return tuple(parsed) if parsed else DEFAULT_FIELDS


def _resolve_provider(db: DbSession, user_id: UUID, workout_key: str) -> str:
    pair = event_record_service.crud.get_with_data_source_by_external_id(db, user_id, workout_key)
    if pair is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workout '{workout_key}' not found for user",
        )
    _, data_source = pair
    provider = data_source.provider.value if hasattr(data_source.provider, "value") else str(data_source.provider)
    if provider not in PROVIDERS_WITH_FIT:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"message": f"Provider '{provider}' does not expose a FIT export", "provider": provider},
        )
    return provider


def _fetch_and_parse(
    db: DbSession,
    provider: str,
    user_id: UUID,
    workout_key: str,
    requested_fields: tuple[str, ...],
) -> tuple[ParsedFit, bytes]:
    fit_bytes = _fetch_fit_bytes(db, provider, user_id, workout_key)
    parsed = parse_fit(fit_bytes, requested_fields)
    return parsed, fit_bytes


def _fetch_fit_bytes(db: DbSession, provider: str, user_id: UUID, workout_key: str) -> bytes:
    cached_bytes = raw_fit_storage.get_fit_bytes(provider, user_id, workout_key)
    if cached_bytes is not None:
        return cached_bytes

    strategy = factory.get_provider(provider)
    workouts = strategy.workouts
    if workouts is None or not hasattr(workouts, "export_workout_fit"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"message": f"Provider '{provider}' has no FIT export helper", "provider": provider},
        )

    export_fit = cast(
        Callable[[DbSession, UUID, str], bytes],
        workouts.export_workout_fit,  # type: ignore[attr-defined]
    )
    fit_bytes = export_fit(db, user_id, workout_key)
    if not fit_bytes:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"{provider} returned an empty FIT body",
        )

    if raw_fit_storage.is_enabled():
        sha = hashlib.sha256(fit_bytes).hexdigest()
        raw_fit_storage.put_fit_bytes(provider, user_id, workout_key, fit_bytes, sha256_hex=sha)

    return fit_bytes
