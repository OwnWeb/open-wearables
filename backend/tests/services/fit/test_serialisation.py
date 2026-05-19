"""Tests for the FIT export JSON / CSV serialisers."""

from datetime import datetime, timezone
from uuid import uuid4

from app.services.fit.parser import Device, Lap, ParsedFit, Sample, Summary
from app.services.fit.serialisation import (
    parsed_fit_to_csv_rows,
    parsed_fit_to_export_response,
)


def _make_parsed_fit() -> ParsedFit:
    start = datetime(2026, 5, 19, 7, 14, 22, tzinfo=timezone.utc)
    second = datetime(2026, 5, 19, 7, 14, 23, tzinfo=timezone.utc)
    return ParsedFit(
        start_time=start,
        end_time=second,
        duration_seconds=1,
        samples=[
            Sample(t=start, elapsed_s=0, values={"heart_rate": 118, "speed": 0.0}),
            Sample(t=second, elapsed_s=1, values={"heart_rate": 121, "speed": 1.4}),
        ],
        laps=[Lap(elapsed_s_start=0, elapsed_s_end=1, avg_heart_rate=120, max_heart_rate=121, total_distance_m=1.4)],
        summary=Summary(avg_heart_rate=120, max_heart_rate=121, total_distance_m=1.4, total_calories=2),
        device=Device(manufacturer="suunto", product="race_2", serial_number="1234567890"),
    )


class TestExportResponse:
    def test_top_level_metadata(self) -> None:
        parsed = _make_parsed_fit()
        user_id = uuid4()

        response = parsed_fit_to_export_response(
            parsed,
            workout_key="WK1",
            provider="suunto",
            user_id=user_id,
            requested_fields=("heart_rate", "speed"),
        )

        assert response["workout_key"] == "WK1"
        assert response["provider"] == "suunto"
        assert response["user_id"] == str(user_id)
        assert response["duration_seconds"] == 1
        assert response["fields"] == ["heart_rate", "speed"]

    def test_samples_carry_requested_fields_only(self) -> None:
        parsed = _make_parsed_fit()

        response = parsed_fit_to_export_response(
            parsed,
            workout_key="WK1",
            provider="suunto",
            user_id=uuid4(),
            requested_fields=("heart_rate",),
        )

        assert len(response["samples"]) == 2
        assert response["samples"][0]["t"].endswith("Z")
        assert response["samples"][0]["heart_rate"] == 118
        assert "speed" not in response["samples"][0]

    def test_summary_and_device_pass_through(self) -> None:
        parsed = _make_parsed_fit()

        response = parsed_fit_to_export_response(
            parsed,
            workout_key="WK1",
            provider="suunto",
            user_id=uuid4(),
            requested_fields=("heart_rate",),
        )

        assert response["summary"]["total_calories"] == 2
        assert response["laps"][0]["avg_heart_rate"] == 120
        assert response["device"]["manufacturer"] == "suunto"


class TestCsvRows:
    def test_header_row_matches_requested_fields(self) -> None:
        parsed = _make_parsed_fit()

        rows = list(parsed_fit_to_csv_rows(parsed, ("heart_rate", "speed")))

        assert rows[0] == ["t", "elapsed_s", "heart_rate", "speed"]

    def test_body_rows_carry_sample_values(self) -> None:
        parsed = _make_parsed_fit()

        rows = list(parsed_fit_to_csv_rows(parsed, ("heart_rate", "speed")))

        assert rows[1][1] == "0"
        assert rows[1][2] == "118"
        assert rows[1][3] == "0.0"
        assert rows[2][2] == "121"

    def test_missing_values_render_as_empty_cells(self) -> None:
        parsed = _make_parsed_fit()
        parsed.samples[0].values.pop("speed")

        rows = list(parsed_fit_to_csv_rows(parsed, ("heart_rate", "speed")))

        assert rows[1] == ["2026-05-19T07:14:22Z", "0", "118", ""]
