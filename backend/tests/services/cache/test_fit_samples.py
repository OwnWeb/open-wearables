"""Tests for the FIT export Redis cache wrapper."""

from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from app.config import settings
from app.services.cache import fit_samples


@pytest.fixture
def mock_redis() -> MagicMock:
    return MagicMock()


@pytest.fixture(autouse=True)
def _patch_redis_client(mock_redis: MagicMock) -> None:
    with patch.object(fit_samples, "get_redis_client", return_value=mock_redis):
        yield


USER_ID = UUID("12345678-1234-5678-1234-567812345678")


class TestCacheKey:
    def test_key_includes_provider_user_workout_and_fields_hash(self) -> None:
        key = fit_samples.cache_key("suunto", USER_ID, "WK1", ("heart_rate",))
        assert key.startswith("fit:workout:suunto:")
        assert str(USER_ID) in key
        assert ":WK1:" in key

    def test_different_fields_produce_different_keys(self) -> None:
        a = fit_samples.cache_key("suunto", USER_ID, "WK1", ("heart_rate",))
        b = fit_samples.cache_key("suunto", USER_ID, "WK1", ("heart_rate", "speed"))
        assert a != b

    def test_fields_order_is_normalised(self) -> None:
        a = fit_samples.cache_key("suunto", USER_ID, "WK1", ("heart_rate", "speed"))
        b = fit_samples.cache_key("suunto", USER_ID, "WK1", ("speed", "heart_rate"))
        assert a == b


class TestGetCachedExport:
    def test_returns_none_when_cache_empty(self, mock_redis: MagicMock) -> None:
        mock_redis.get.return_value = None

        result = fit_samples.get_cached_export("suunto", USER_ID, "WK1", ("heart_rate",))

        assert result is None

    def test_decodes_json_payload(self, mock_redis: MagicMock) -> None:
        mock_redis.get.return_value = '{"workout_key": "WK1", "fields": ["heart_rate"]}'

        result = fit_samples.get_cached_export("suunto", USER_ID, "WK1", ("heart_rate",))

        assert result == {"workout_key": "WK1", "fields": ["heart_rate"]}

    def test_swallows_corrupted_json(self, mock_redis: MagicMock) -> None:
        mock_redis.get.return_value = "{not-json"

        assert fit_samples.get_cached_export("suunto", USER_ID, "WK1", ("heart_rate",)) is None

    def test_swallows_redis_exception(self, mock_redis: MagicMock) -> None:
        mock_redis.get.side_effect = RuntimeError("redis down")

        assert fit_samples.get_cached_export("suunto", USER_ID, "WK1", ("heart_rate",)) is None


class TestSetCachedExport:
    def test_writes_with_configured_ttl(self, mock_redis: MagicMock) -> None:
        fit_samples.set_cached_export(
            "suunto",
            USER_ID,
            "WK1",
            ("heart_rate",),
            {"workout_key": "WK1"},
        )

        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert call_args[0][1] == settings.fit_export_cache_ttl_seconds
        assert call_args[0][2].startswith("{")

    def test_swallows_redis_exception(self, mock_redis: MagicMock) -> None:
        mock_redis.setex.side_effect = RuntimeError("redis down")

        # Should not raise.
        fit_samples.set_cached_export("suunto", USER_ID, "WK1", ("heart_rate",), {})
