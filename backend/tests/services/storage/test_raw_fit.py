"""Tests for the L2 S3 raw FIT storage backend."""

import gzip
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from app.services.storage import raw_fit

USER_ID = UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture(autouse=True)
def _reset_module_state() -> None:
    """Reset module-level globals before each test."""
    raw_fit._enabled = False
    raw_fit._s3_bucket = None
    raw_fit._s3_prefix = "raw-fit"
    raw_fit._s3_client = None


class TestConfigure:
    def test_disabled_when_flag_off(self) -> None:
        raw_fit.configure(enabled=False, s3_bucket="any")
        assert raw_fit.is_enabled() is False

    def test_disabled_when_bucket_missing(self) -> None:
        raw_fit.configure(enabled=True, s3_bucket=None)
        assert raw_fit.is_enabled() is False

    def test_enabled_with_bucket_and_client(self) -> None:
        mock_client = MagicMock()
        with patch.object(raw_fit, "_create_s3_client", return_value=mock_client):
            raw_fit.configure(enabled=True, s3_bucket="my-bucket", s3_prefix="raw-fit")

        assert raw_fit.is_enabled() is True
        assert raw_fit._s3_bucket == "my-bucket"
        assert raw_fit._s3_client is mock_client

    def test_disabled_when_client_creation_fails(self) -> None:
        with patch.object(raw_fit, "_create_s3_client", return_value=None):
            raw_fit.configure(enabled=True, s3_bucket="my-bucket")

        assert raw_fit.is_enabled() is False


class TestGetFitBytes:
    def test_returns_none_when_disabled(self) -> None:
        assert raw_fit.get_fit_bytes("suunto", USER_ID, "WK1") is None

    def test_returns_decompressed_bytes_on_hit(self) -> None:
        original = b"raw-fit-bytes"
        mock_client = MagicMock()
        mock_client.get_object.return_value = {"Body": MagicMock(read=lambda: gzip.compress(original))}

        with patch.object(raw_fit, "_create_s3_client", return_value=mock_client):
            raw_fit.configure(enabled=True, s3_bucket="my-bucket")

        result = raw_fit.get_fit_bytes("suunto", USER_ID, "WK1")
        assert result == original
        mock_client.get_object.assert_called_once()
        call_kwargs = mock_client.get_object.call_args[1]
        assert call_kwargs["Bucket"] == "my-bucket"
        assert call_kwargs["Key"] == f"raw-fit/suunto/{USER_ID}/WK1.fit.gz"

    def test_returns_none_on_missing_key(self) -> None:
        mock_client = MagicMock()

        class _NoSuchKeyError(Exception):
            pass

        mock_client.exceptions.NoSuchKey = _NoSuchKeyError
        mock_client.get_object.side_effect = _NoSuchKeyError()

        with patch.object(raw_fit, "_create_s3_client", return_value=mock_client):
            raw_fit.configure(enabled=True, s3_bucket="my-bucket")

        assert raw_fit.get_fit_bytes("suunto", USER_ID, "WK1") is None


class TestPutFitBytes:
    def test_noop_when_disabled(self) -> None:
        # Should not raise even though no client is configured.
        raw_fit.put_fit_bytes("suunto", USER_ID, "WK1", b"raw")

    def test_uploads_gzipped_body_with_metadata(self) -> None:
        mock_client = MagicMock()

        with patch.object(raw_fit, "_create_s3_client", return_value=mock_client):
            raw_fit.configure(enabled=True, s3_bucket="my-bucket", s3_prefix="raw-fit")

        raw_fit.put_fit_bytes("suunto", USER_ID, "WK1", b"raw-fit-bytes", sha256_hex="abc")

        mock_client.put_object.assert_called_once()
        call_kwargs = mock_client.put_object.call_args[1]
        assert call_kwargs["Bucket"] == "my-bucket"
        assert call_kwargs["Key"] == f"raw-fit/suunto/{USER_ID}/WK1.fit.gz"
        assert call_kwargs["ContentType"] == "application/vnd.ant.fit"
        assert call_kwargs["ContentEncoding"] == "gzip"
        assert gzip.decompress(call_kwargs["Body"]) == b"raw-fit-bytes"
        assert call_kwargs["Metadata"]["provider"] == "suunto"
        assert call_kwargs["Metadata"]["sha256"] == "abc"

    def test_handles_upload_error_silently(self) -> None:
        mock_client = MagicMock()
        mock_client.put_object.side_effect = RuntimeError("S3 down")

        with patch.object(raw_fit, "_create_s3_client", return_value=mock_client):
            raw_fit.configure(enabled=True, s3_bucket="my-bucket")

        # Should not raise.
        raw_fit.put_fit_bytes("suunto", USER_ID, "WK1", b"raw")


class TestDeleteForUser:
    def test_noop_when_disabled(self) -> None:
        assert raw_fit.delete_for_user(USER_ID) == 0

    def test_walks_each_provider_prefix(self) -> None:
        mock_client = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.side_effect = [
            # Provider listing pass
            iter([{"CommonPrefixes": [{"Prefix": "raw-fit/suunto/"}, {"Prefix": "raw-fit/garmin/"}]}]),
            # Suunto user objects
            iter([{"Contents": [{"Key": f"raw-fit/suunto/{USER_ID}/WK1.fit.gz"}]}]),
            # Garmin user objects (none)
            iter([{"Contents": []}]),
        ]
        mock_client.get_paginator.return_value = mock_paginator

        with patch.object(raw_fit, "_create_s3_client", return_value=mock_client):
            raw_fit.configure(enabled=True, s3_bucket="my-bucket", s3_prefix="raw-fit")

        deleted = raw_fit.delete_for_user(USER_ID)

        assert deleted == 1
        mock_client.delete_objects.assert_called_once()
