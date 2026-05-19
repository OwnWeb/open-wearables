"""Garmin activityFiles webhook handler.

Garmin delivers per-activity file downloads (FIT/TCX/GPX) as PING-only
notifications. Each notification carries a signed callback URL and the
file type. We download the FIT immediately and persist the bytes to L2
so the /export endpoint can serve them later. Garmin's signed URLs have
a short TTL, so missing this window means the FIT is unreachable.
"""

import logging
from typing import Any

from fastapi import HTTPException

from app.database import DbSession
from app.repositories import UserConnectionRepository
from app.services.providers.garmin.workouts import GarminWorkouts
from app.services.storage import raw_fit as raw_fit_storage
from app.utils.structured_logging import log_structured

logger = logging.getLogger(__name__)

FIT_FILE_TYPE = "FIT"


def process_activity_file_notification(
    db: DbSession,
    connection_repo: UserConnectionRepository,
    garmin_workouts: GarminWorkouts,
    notification: dict[str, Any],
    request_trace_id: str,
) -> dict[str, Any]:
    """Process a single Garmin activityFiles PING notification."""
    garmin_user_id: str | None = notification.get("userId")
    activity_id = notification.get("activityId") or notification.get("summaryId")
    file_type = notification.get("fileType")
    callback_url = notification.get("callbackURL")

    base_result: dict[str, Any] = {
        "activity_id": activity_id,
        "file_type": file_type,
        "garmin_user_id": garmin_user_id,
    }

    if not garmin_user_id or not activity_id or not callback_url:
        return {**base_result, "status": "error", "error": "Missing required fields"}

    if file_type != FIT_FILE_TYPE:
        # Only FIT is ingested; TCX/GPX are duplicate signals for the same activity.
        return {**base_result, "status": "skipped", "reason": "non_fit_file_type"}

    if not raw_fit_storage.is_enabled():
        return {**base_result, "status": "skipped", "reason": "l2_disabled"}

    connection = connection_repo.get_by_provider_user_id(db, "garmin", garmin_user_id)
    if not connection:
        log_structured(
            logger,
            "warning",
            "No connection found for Garmin user",
            provider="garmin",
            trace_id=request_trace_id,
            garmin_user_id=garmin_user_id,
        )
        return {**base_result, "status": "user_not_found", "error": f"User {garmin_user_id} not connected"}

    internal_user_id = connection.user_id

    try:
        size_bytes = garmin_workouts.ingest_fit_for_activity(
            db,
            internal_user_id,
            str(activity_id),
            callback_url,
        )
    except HTTPException as e:
        log_structured(
            logger,
            "error",
            "Failed to ingest Garmin FIT",
            provider="garmin",
            trace_id=request_trace_id,
            activity_id=activity_id,
            user_id=str(internal_user_id),
            status_code=e.status_code,
            detail=str(e.detail)[:200],
        )
        return {
            **base_result,
            "internal_user_id": str(internal_user_id),
            "status": "fetch_failed",
            "error": str(e.detail)[:200],
        }

    log_structured(
        logger,
        "info",
        "Stored Garmin FIT to L2",
        provider="garmin",
        trace_id=request_trace_id,
        activity_id=activity_id,
        user_id=str(internal_user_id),
        size_bytes=size_bytes,
    )
    return {
        **base_result,
        "internal_user_id": str(internal_user_id),
        "status": "saved",
        "size_bytes": size_bytes,
    }
