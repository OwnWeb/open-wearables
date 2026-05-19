"""Tests for the workout FIT export endpoints.

Tests the following endpoints:
- GET /api/v1/users/{user_id}/workouts/{workout_key}/export
- GET /api/v1/users/{user_id}/workouts/{workout_key}/export.csv
- GET /api/v1/users/{user_id}/workouts/{workout_key}/export.fit
"""

import hashlib
from datetime import datetime, timezone
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.schemas.enums import ProviderName
from app.services.fit.parser import Device, Lap, ParsedFit, Sample, Summary
from tests.factories import (
    ApiKeyFactory,
    DataSourceFactory,
    EventRecordFactory,
    UserFactory,
)

FIT_BYTES = b"\x0e\x10raw-suunto-fit-bytes"
WORKOUT_KEY = "WK-12345"


def _build_parsed_fit() -> ParsedFit:
    start = datetime(2026, 5, 19, 7, 14, 22, tzinfo=timezone.utc)
    second = datetime(2026, 5, 19, 7, 14, 23, tzinfo=timezone.utc)
    return ParsedFit(
        start_time=start,
        end_time=second,
        duration_seconds=1,
        samples=[
            Sample(t=start, elapsed_s=0, values={"heart_rate": 118}),
            Sample(t=second, elapsed_s=1, values={"heart_rate": 121}),
        ],
        laps=[Lap(elapsed_s_start=0, elapsed_s_end=1, avg_heart_rate=120, max_heart_rate=121, total_distance_m=1.4)],
        summary=Summary(avg_heart_rate=120, max_heart_rate=121, total_distance_m=1.4, total_calories=2),
        device=Device(manufacturer="suunto", product="race_2", serial_number="1234567890"),
    )


class TestWorkoutExportEndpoints:
    """Test suite for workout export endpoints."""

    @pytest.fixture
    def mock_provider_factory(self) -> Generator[MagicMock, None, None]:
        """Mock the ProviderFactory to avoid external API calls."""
        with patch("app.api.routes.v1.workout_export.factory") as mock_factory:
            mock_strategy = MagicMock()
            mock_strategy.workouts.export_workout_fit.return_value = FIT_BYTES
            mock_factory.get_provider.return_value = mock_strategy
            yield mock_factory

    @pytest.fixture
    def mock_fit_parser(self) -> Generator[MagicMock, None, None]:
        """Stub fitdecode parse with a canned ParsedFit."""
        with patch(
            "app.api.routes.v1.workout_export.parse_fit",
            return_value=_build_parsed_fit(),
        ) as mock:
            yield mock

    @pytest.fixture
    def mock_fit_cache(self) -> Generator[MagicMock, None, None]:
        """Default: empty L1 cache, capture writes."""
        with (
            patch("app.api.routes.v1.workout_export.fit_cache.get_cached_export", return_value=None) as get_mock,
            patch("app.api.routes.v1.workout_export.fit_cache.set_cached_export") as set_mock,
        ):
            yield MagicMock(get=get_mock, set=set_mock)

    @pytest.fixture
    def mock_raw_fit_storage(self) -> Generator[MagicMock, None, None]:
        """Default: L2 disabled, no cache writes."""
        with (
            patch("app.api.routes.v1.workout_export.raw_fit_storage.get_fit_bytes", return_value=None),
            patch("app.api.routes.v1.workout_export.raw_fit_storage.is_enabled", return_value=False) as enabled,
            patch("app.api.routes.v1.workout_export.raw_fit_storage.put_fit_bytes") as put_mock,
        ):
            yield MagicMock(is_enabled=enabled, put=put_mock)

    def _seed_suunto_workout(self, db: Session) -> tuple:
        """Create user + suunto data_source + event_record. Returns (user, data_source, event_record)."""
        user = UserFactory()
        data_source = DataSourceFactory(user=user, provider=ProviderName.SUUNTO, source="suunto")
        event_record = EventRecordFactory(data_source=data_source, external_id=WORKOUT_KEY, category="workout")
        return user, data_source, event_record

    def _seed_polar_workout(self, db: Session) -> tuple:
        """Create user + polar data_source + event_record."""
        user = UserFactory()
        data_source = DataSourceFactory(user=user, provider=ProviderName.POLAR, source="polar")
        event_record = EventRecordFactory(data_source=data_source, external_id=WORKOUT_KEY, category="workout")
        return user, data_source, event_record

    def test_export_json_unauthorized(self, client: TestClient, db: Session) -> None:
        """Test that missing API key returns 401."""
        # Arrange
        user, _, _ = self._seed_suunto_workout(db)

        # Act
        response = client.get(f"/api/v1/users/{user.id}/workouts/{WORKOUT_KEY}/export")

        # Assert
        assert response.status_code == 401

    def test_export_json_unknown_workout_returns_404(self, client: TestClient, db: Session) -> None:
        """Test that an unknown (user, workout_key) pair returns 404."""
        # Arrange
        user = UserFactory()
        api_key = ApiKeyFactory()

        # Act
        response = client.get(
            f"/api/v1/users/{user.id}/workouts/unknown-key/export",
            headers={"X-Open-Wearables-API-Key": api_key.id},
        )

        # Assert
        assert response.status_code == 404

    def test_export_json_non_suunto_provider_returns_415(self, client: TestClient, db: Session) -> None:
        """Test that an event_record from a non-FIT provider returns 415."""
        # Arrange
        user = UserFactory()
        api_key = ApiKeyFactory()
        data_source = DataSourceFactory(user=user, provider=ProviderName.OURA, source="oura")
        EventRecordFactory(data_source=data_source, external_id=WORKOUT_KEY, category="workout")

        # Act
        response = client.get(
            f"/api/v1/users/{user.id}/workouts/{WORKOUT_KEY}/export",
            headers={"X-Open-Wearables-API-Key": api_key.id},
        )

        # Assert
        assert response.status_code == 415
        body = response.json()
        assert body["detail"]["provider"] == "oura"

    def test_export_json_happy_path(
        self,
        client: TestClient,
        db: Session,
        mock_provider_factory: MagicMock,
        mock_fit_parser: MagicMock,
        mock_fit_cache: MagicMock,
        mock_raw_fit_storage: MagicMock,
    ) -> None:
        """Test that a valid Suunto workout returns parsed JSON."""
        # Arrange
        user, _, _ = self._seed_suunto_workout(db)
        api_key = ApiKeyFactory()

        # Act
        response = client.get(
            f"/api/v1/users/{user.id}/workouts/{WORKOUT_KEY}/export",
            headers={"X-Open-Wearables-API-Key": api_key.id},
        )

        # Assert
        assert response.status_code == 200
        data = response.json()
        assert data["workout_key"] == WORKOUT_KEY
        assert data["provider"] == "suunto"
        assert len(data["samples"]) == 2
        assert data["samples"][0]["heart_rate"] == 118
        mock_provider_factory.get_provider.assert_called_once_with("suunto")
        mock_fit_cache.set.assert_called_once()

    def test_export_json_l1_cache_hit_skips_provider(
        self,
        client: TestClient,
        db: Session,
        mock_provider_factory: MagicMock,
    ) -> None:
        """Test that an L1 hit serves the cached response without calling the provider."""
        # Arrange
        user, _, _ = self._seed_suunto_workout(db)
        api_key = ApiKeyFactory()
        cached_payload = {
            "workout_key": WORKOUT_KEY,
            "provider": "suunto",
            "user_id": str(user.id),
            "fields": ["heart_rate"],
            "samples": [{"t": "2026-05-19T07:14:22Z", "elapsed_s": 0, "heart_rate": 142}],
            "laps": [],
            "summary": {"avg_heart_rate": 142, "max_heart_rate": 142, "total_distance_m": None, "total_calories": None},
            "device": {"manufacturer": "suunto", "product": "race_2", "serial_number": None},
            "duration_seconds": 0,
            "start_time": None,
            "end_time": None,
        }

        with patch(
            "app.api.routes.v1.workout_export.fit_cache.get_cached_export",
            return_value=cached_payload,
        ):
            # Act
            response = client.get(
                f"/api/v1/users/{user.id}/workouts/{WORKOUT_KEY}/export",
                headers={"X-Open-Wearables-API-Key": api_key.id},
            )

        # Assert
        assert response.status_code == 200
        body = response.json()
        assert body["workout_key"] == WORKOUT_KEY
        assert body["samples"] == cached_payload["samples"]
        mock_provider_factory.get_provider.assert_not_called()

    def test_export_json_l2_cache_hit_skips_provider_call(
        self,
        client: TestClient,
        db: Session,
        mock_fit_parser: MagicMock,
        mock_fit_cache: MagicMock,
    ) -> None:
        """Test that an L2 hit feeds the parser directly without contacting the provider."""
        # Arrange
        user, _, _ = self._seed_suunto_workout(db)
        api_key = ApiKeyFactory()

        with (
            patch("app.api.routes.v1.workout_export.factory") as mock_factory,
            patch(
                "app.api.routes.v1.workout_export.raw_fit_storage.get_fit_bytes",
                return_value=FIT_BYTES,
            ),
        ):
            mock_factory.get_provider.return_value = MagicMock()

            # Act
            response = client.get(
                f"/api/v1/users/{user.id}/workouts/{WORKOUT_KEY}/export",
                headers={"X-Open-Wearables-API-Key": api_key.id},
            )

        # Assert
        assert response.status_code == 200
        mock_factory.get_provider.assert_not_called()

    def test_export_json_invalid_field_returns_400(
        self,
        client: TestClient,
        db: Session,
    ) -> None:
        """Test that an unknown field in ?fields= returns 400."""
        # Arrange
        user, _, _ = self._seed_suunto_workout(db)
        api_key = ApiKeyFactory()

        # Act
        response = client.get(
            f"/api/v1/users/{user.id}/workouts/{WORKOUT_KEY}/export?fields=bogus",
            headers={"X-Open-Wearables-API-Key": api_key.id},
        )

        # Assert
        assert response.status_code == 400

    def test_export_csv_returns_csv_content_type(
        self,
        client: TestClient,
        db: Session,
        mock_provider_factory: MagicMock,
        mock_fit_parser: MagicMock,
        mock_raw_fit_storage: MagicMock,
    ) -> None:
        """Test that .csv returns the expected content-type and body shape."""
        # Arrange
        user, _, _ = self._seed_suunto_workout(db)
        api_key = ApiKeyFactory()

        # Act
        response = client.get(
            f"/api/v1/users/{user.id}/workouts/{WORKOUT_KEY}/export.csv?fields=heart_rate",
            headers={"X-Open-Wearables-API-Key": api_key.id},
        )

        # Assert
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "attachment" in response.headers["content-disposition"]
        lines = response.text.strip().splitlines()
        assert lines[0] == "t,elapsed_s,heart_rate"
        assert lines[1].endswith(",118")

    def test_export_fit_returns_raw_bytes_with_etag(
        self,
        client: TestClient,
        db: Session,
        mock_provider_factory: MagicMock,
        mock_raw_fit_storage: MagicMock,
    ) -> None:
        """Test that .fit returns the binary FIT body + ETag."""
        # Arrange
        user, _, _ = self._seed_suunto_workout(db)
        api_key = ApiKeyFactory()
        expected_etag = f'"{hashlib.sha256(FIT_BYTES).hexdigest()}"'

        # Act
        response = client.get(
            f"/api/v1/users/{user.id}/workouts/{WORKOUT_KEY}/export.fit",
            headers={"X-Open-Wearables-API-Key": api_key.id},
        )

        # Assert
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/vnd.ant.fit"
        assert response.headers["etag"] == expected_etag
        assert response.content == FIT_BYTES

    def test_export_fit_returns_304_on_matching_etag(
        self,
        client: TestClient,
        db: Session,
        mock_provider_factory: MagicMock,
        mock_raw_fit_storage: MagicMock,
    ) -> None:
        """Test that a matching If-None-Match returns 304 with no body."""
        # Arrange
        user, _, _ = self._seed_suunto_workout(db)
        api_key = ApiKeyFactory()
        etag = f'"{hashlib.sha256(FIT_BYTES).hexdigest()}"'

        # Act
        response = client.get(
            f"/api/v1/users/{user.id}/workouts/{WORKOUT_KEY}/export.fit",
            headers={"X-Open-Wearables-API-Key": api_key.id, "If-None-Match": etag},
        )

        # Assert
        assert response.status_code == 304
        assert response.content == b""

    def test_export_persists_l2_on_provider_fetch(
        self,
        client: TestClient,
        db: Session,
        mock_provider_factory: MagicMock,
        mock_fit_parser: MagicMock,
        mock_fit_cache: MagicMock,
    ) -> None:
        """Test that a provider fetch persists FIT bytes to L2 when storage is enabled."""
        # Arrange
        user, _, _ = self._seed_suunto_workout(db)
        api_key = ApiKeyFactory()

        with (
            patch("app.api.routes.v1.workout_export.raw_fit_storage.get_fit_bytes", return_value=None),
            patch("app.api.routes.v1.workout_export.raw_fit_storage.is_enabled", return_value=True),
            patch("app.api.routes.v1.workout_export.raw_fit_storage.put_fit_bytes") as put_mock,
        ):
            # Act
            response = client.get(
                f"/api/v1/users/{user.id}/workouts/{WORKOUT_KEY}/export",
                headers={"X-Open-Wearables-API-Key": api_key.id},
            )

        # Assert
        assert response.status_code == 200
        put_mock.assert_called_once()
        _, kwargs = put_mock.call_args
        assert kwargs["sha256_hex"] == hashlib.sha256(FIT_BYTES).hexdigest()

    def test_export_json_polar_happy_path(
        self,
        client: TestClient,
        db: Session,
        mock_fit_parser: MagicMock,
        mock_fit_cache: MagicMock,
        mock_raw_fit_storage: MagicMock,
    ) -> None:
        """Test that a Polar exercise dispatches via export_workout_fit."""
        # Arrange
        user, _, _ = self._seed_polar_workout(db)
        api_key = ApiKeyFactory()

        with patch("app.api.routes.v1.workout_export.factory") as mock_factory:
            mock_strategy = MagicMock()
            mock_strategy.workouts.export_workout_fit.return_value = FIT_BYTES
            mock_factory.get_provider.return_value = mock_strategy

            # Act
            response = client.get(
                f"/api/v1/users/{user.id}/workouts/{WORKOUT_KEY}/export",
                headers={"X-Open-Wearables-API-Key": api_key.id},
            )

        # Assert
        assert response.status_code == 200
        body = response.json()
        assert body["provider"] == "polar"
        mock_factory.get_provider.assert_called_once_with("polar")
