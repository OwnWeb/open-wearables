"""Tests for the Garmin activityFiles PING handler + FIT ingestion."""

from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import EventRecord
from app.repositories.event_record_repository import EventRecordRepository
from app.repositories.user_connection_repository import UserConnectionRepository
from app.schemas.auth import ConnectionStatus
from app.services.providers.garmin.handlers.activity_files import process_activity_file_notification
from app.services.providers.garmin.oauth import GarminOAuth
from app.services.providers.garmin.workouts import GarminWorkouts
from tests.factories import UserConnectionFactory, UserFactory

FIT_BYTES = b"\x0e\x10raw-garmin-fit-bytes"
GARMIN_USER_ID = "garmin_user_42"
ACTIVITY_ID = "98765432101"
CALLBACK_URL = "https://apis.garmin.com/wellness-api/wellness/activityFile?id=signed"


class TestProcessActivityFileNotification:
    """Test suite for the activityFiles PING handler."""

    @pytest.fixture
    def garmin_workouts(self, db: Session) -> GarminWorkouts:
        workout_repo = EventRecordRepository(EventRecord)
        connection_repo = UserConnectionRepository()
        oauth = GarminOAuth(
            user_repo=MagicMock(),
            connection_repo=connection_repo,
            provider_name="garmin",
            api_base_url="https://apis.garmin.com",
        )
        return GarminWorkouts(
            workout_repo=workout_repo,
            connection_repo=connection_repo,
            provider_name="garmin",
            api_base_url="https://apis.garmin.com",
            oauth=oauth,
        )

    def _ping_notification(self, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "userId": GARMIN_USER_ID,
            "activityId": ACTIVITY_ID,
            "fileType": "FIT",
            "callbackURL": CALLBACK_URL,
        }
        base.update(overrides)
        return base

    def _seed_connection(self, db: Session) -> Any:
        user = UserFactory()
        return UserConnectionFactory(
            user=user,
            provider="garmin",
            provider_user_id=GARMIN_USER_ID,
            status=ConnectionStatus.ACTIVE,
        )

    def test_missing_required_fields_returns_error(
        self, garmin_workouts: GarminWorkouts, db: Session
    ) -> None:
        # Arrange
        notification = self._ping_notification(callbackURL=None)

        # Act
        result = process_activity_file_notification(
            db, UserConnectionRepository(), garmin_workouts, notification, "trace-1"
        )

        # Assert
        assert result["status"] == "error"

    def test_non_fit_file_type_is_skipped(
        self, garmin_workouts: GarminWorkouts, db: Session
    ) -> None:
        # Arrange
        notification = self._ping_notification(fileType="TCX")

        # Act
        result = process_activity_file_notification(
            db, UserConnectionRepository(), garmin_workouts, notification, "trace-1"
        )

        # Assert
        assert result["status"] == "skipped"
        assert result["reason"] == "non_fit_file_type"

    def test_l2_disabled_is_skipped(
        self, garmin_workouts: GarminWorkouts, db: Session
    ) -> None:
        # Arrange
        notification = self._ping_notification()

        # Act
        with patch(
            "app.services.providers.garmin.handlers.activity_files.raw_fit_storage.is_enabled",
            return_value=False,
        ):
            result = process_activity_file_notification(
                db, UserConnectionRepository(), garmin_workouts, notification, "trace-1"
            )

        # Assert
        assert result["status"] == "skipped"
        assert result["reason"] == "l2_disabled"

    def test_unknown_garmin_user_returns_user_not_found(
        self, garmin_workouts: GarminWorkouts, db: Session
    ) -> None:
        # Arrange
        notification = self._ping_notification()

        # Act
        with patch(
            "app.services.providers.garmin.handlers.activity_files.raw_fit_storage.is_enabled",
            return_value=True,
        ):
            result = process_activity_file_notification(
                db, UserConnectionRepository(), garmin_workouts, notification, "trace-1"
            )

        # Assert
        assert result["status"] == "user_not_found"

    def test_happy_path_downloads_and_stores_fit(
        self, garmin_workouts: GarminWorkouts, db: Session
    ) -> None:
        # Arrange
        connection = self._seed_connection(db)
        notification = self._ping_notification()

        # Act
        with (
            patch(
                "app.services.providers.garmin.handlers.activity_files.raw_fit_storage.is_enabled",
                return_value=True,
            ),
            patch.object(
                garmin_workouts,
                "ingest_fit_for_activity",
                return_value=len(FIT_BYTES),
            ) as ingest_mock,
        ):
            result = process_activity_file_notification(
                db, UserConnectionRepository(), garmin_workouts, notification, "trace-1"
            )

        # Assert
        assert result["status"] == "saved"
        assert result["size_bytes"] == len(FIT_BYTES)
        ingest_mock.assert_called_once_with(db, connection.user_id, ACTIVITY_ID, CALLBACK_URL)

    def test_fetch_failure_returns_fetch_failed(
        self, garmin_workouts: GarminWorkouts, db: Session
    ) -> None:
        # Arrange
        self._seed_connection(db)
        notification = self._ping_notification()

        # Act
        with (
            patch(
                "app.services.providers.garmin.handlers.activity_files.raw_fit_storage.is_enabled",
                return_value=True,
            ),
            patch.object(
                garmin_workouts,
                "ingest_fit_for_activity",
                side_effect=HTTPException(status_code=502, detail="Garmin upstream error"),
            ),
        ):
            result = process_activity_file_notification(
                db, UserConnectionRepository(), garmin_workouts, notification, "trace-1"
            )

        # Assert
        assert result["status"] == "fetch_failed"


class TestGarminExportWorkoutFit:
    """Test suite for GarminWorkouts.export_workout_fit (L2-only read path)."""

    @pytest.fixture
    def garmin_workouts(self, db: Session) -> GarminWorkouts:
        workout_repo = EventRecordRepository(EventRecord)
        connection_repo = UserConnectionRepository()
        oauth = GarminOAuth(
            user_repo=MagicMock(),
            connection_repo=connection_repo,
            provider_name="garmin",
            api_base_url="https://apis.garmin.com",
        )
        return GarminWorkouts(
            workout_repo=workout_repo,
            connection_repo=connection_repo,
            provider_name="garmin",
            api_base_url="https://apis.garmin.com",
            oauth=oauth,
        )

    def test_raises_415_when_l2_disabled(self, garmin_workouts: GarminWorkouts, db: Session) -> None:
        # Act
        with patch(
            "app.services.providers.garmin.workouts.raw_fit_storage.is_enabled",
            return_value=False,
        ), pytest.raises(HTTPException) as exc:
            garmin_workouts.export_workout_fit(db, uuid4(), ACTIVITY_ID)

        # Assert
        assert exc.value.status_code == 415

    def test_raises_425_when_l2_miss(self, garmin_workouts: GarminWorkouts, db: Session) -> None:
        # Act
        with (
            patch(
                "app.services.providers.garmin.workouts.raw_fit_storage.is_enabled",
                return_value=True,
            ),
            patch(
                "app.services.providers.garmin.workouts.raw_fit_storage.get_fit_bytes",
                return_value=None,
            ),pytest.raises(HTTPException) as exc
        ):
            garmin_workouts.export_workout_fit(db, uuid4(), ACTIVITY_ID)

        # Assert
        assert exc.value.status_code == 425

    def test_returns_l2_bytes_on_hit(self, garmin_workouts: GarminWorkouts, db: Session) -> None:
        # Act
        with (
            patch(
                "app.services.providers.garmin.workouts.raw_fit_storage.is_enabled",
                return_value=True,
            ),
            patch(
                "app.services.providers.garmin.workouts.raw_fit_storage.get_fit_bytes",
                return_value=FIT_BYTES,
            ),
        ):
            result = garmin_workouts.export_workout_fit(db, uuid4(), ACTIVITY_ID)

        # Assert
        assert result == FIT_BYTES
