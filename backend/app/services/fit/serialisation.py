"""Map ParsedFit dataclasses to the JSON / CSV response shapes."""

from __future__ import annotations

from typing import Any, Iterable, Iterator
from uuid import UUID

from app.services.fit.parser import ParsedFit


def parsed_fit_to_export_response(
    parsed: ParsedFit,
    *,
    workout_key: str,
    provider: str,
    user_id: UUID,
    requested_fields: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "workout_key": workout_key,
        "provider": provider,
        "user_id": str(user_id),
        "start_time": _iso(parsed.start_time),
        "end_time": _iso(parsed.end_time),
        "duration_seconds": parsed.duration_seconds,
        "device": {
            "manufacturer": parsed.device.manufacturer,
            "product": parsed.device.product,
            "serial_number": parsed.device.serial_number,
        },
        "fields": list(requested_fields),
        "samples": [
            {
                "t": _iso(sample.t),
                "elapsed_s": sample.elapsed_s,
                **{key: sample.values.get(key) for key in requested_fields},
            }
            for sample in parsed.samples
        ],
        "laps": [
            {
                "elapsed_s_start": lap.elapsed_s_start,
                "elapsed_s_end": lap.elapsed_s_end,
                "avg_heart_rate": lap.avg_heart_rate,
                "max_heart_rate": lap.max_heart_rate,
                "total_distance_m": lap.total_distance_m,
            }
            for lap in parsed.laps
        ],
        "summary": {
            "avg_heart_rate": parsed.summary.avg_heart_rate,
            "max_heart_rate": parsed.summary.max_heart_rate,
            "total_distance_m": parsed.summary.total_distance_m,
            "total_calories": parsed.summary.total_calories,
        },
    }


def parsed_fit_to_csv_rows(
    parsed: ParsedFit,
    requested_fields: Iterable[str],
) -> Iterator[list[str]]:
    fields = tuple(requested_fields)
    yield ["t", "elapsed_s", *fields]
    for sample in parsed.samples:
        row = [_iso(sample.t) or "", str(sample.elapsed_s)]
        for key in fields:
            value = sample.values.get(key)
            row.append("" if value is None else _csv_value(value))
        yield row


def _csv_value(value: Any) -> str:
    if isinstance(value, list):
        return ";".join(str(part) for part in value)
    return str(value)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")
