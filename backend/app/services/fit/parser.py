"""FIT file parser. Thin wrapper around fitdecode kept behind a dataclass interface
so a swap to another lib stays contained to this file."""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import fitdecode

from app.services.fit.field_map import (
    CANONICAL_FIELDS,
    LATLNG_FIELD,
    LATLNG_FIT_NAMES,
    semicircle_to_degree,
)

logger = logging.getLogger(__name__)


@dataclass
class Device:
    manufacturer: str | None = None
    product: str | None = None
    serial_number: str | None = None


@dataclass
class Sample:
    t: datetime
    elapsed_s: int
    values: dict[str, Any] = field(default_factory=dict)


@dataclass
class Lap:
    elapsed_s_start: int
    elapsed_s_end: int
    avg_heart_rate: int | None = None
    max_heart_rate: int | None = None
    total_distance_m: float | None = None


@dataclass
class Summary:
    avg_heart_rate: int | None = None
    max_heart_rate: int | None = None
    total_distance_m: float | None = None
    total_calories: int | None = None


@dataclass
class ParsedFit:
    start_time: datetime | None = None
    end_time: datetime | None = None
    samples: list[Sample] = field(default_factory=list)
    laps: list[Lap] = field(default_factory=list)
    summary: Summary = field(default_factory=Summary)
    device: Device = field(default_factory=Device)
    duration_seconds: int = 0


def parse_fit(
    fit_bytes: bytes,
    fields: Iterable[str],
    include_laps: bool = True,
    include_summary: bool = True,
) -> ParsedFit:
    parsed = ParsedFit()
    requested = tuple(fields)

    with fitdecode.FitReader(
        io.BytesIO(fit_bytes),
        check_crc=fitdecode.CrcCheck.WARN,
        keep_raw_chunks=False,
    ) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            _handle_frame(frame, parsed, requested, include_laps, include_summary)

    _finalise(parsed)
    return parsed


def _handle_frame(
    frame: fitdecode.FitDataMessage,
    parsed: ParsedFit,
    requested_fields: tuple[str, ...],
    include_laps: bool,
    include_summary: bool,
) -> None:
    name = frame.name
    if name == "record":
        _read_record(frame, parsed, requested_fields)
    elif name == "lap" and include_laps:
        _read_lap(frame, parsed)
    elif name == "session" and include_summary:
        _read_session(frame, parsed)
    elif name == "file_id":
        _read_file_id(frame, parsed)
    elif name == "device_info":
        _read_device_info(frame, parsed)


def _read_record(
    frame: fitdecode.FitDataMessage,
    parsed: ParsedFit,
    requested_fields: tuple[str, ...],
) -> None:
    timestamp = _get(frame, "timestamp")
    if not isinstance(timestamp, datetime):
        return
    timestamp = _ensure_utc(timestamp)

    if parsed.start_time is None:
        parsed.start_time = timestamp

    values: dict[str, Any] = {}
    for canonical in requested_fields:
        value = _extract_canonical(frame, canonical)
        if value is not None:
            values[canonical] = value

    elapsed = int((timestamp - parsed.start_time).total_seconds())
    parsed.samples.append(Sample(t=timestamp, elapsed_s=elapsed, values=values))


def _extract_canonical(frame: fitdecode.FitDataMessage, canonical: str) -> Any:
    if canonical == LATLNG_FIELD:
        lat = _get(frame, LATLNG_FIT_NAMES[0])
        lng = _get(frame, LATLNG_FIT_NAMES[1])
        if lat is None or lng is None:
            return None
        return [semicircle_to_degree(int(lat)), semicircle_to_degree(int(lng))]

    for fit_name in CANONICAL_FIELDS.get(canonical, ()):
        value = _get(frame, fit_name)
        if value is not None:
            return value
    return None


def _read_lap(frame: fitdecode.FitDataMessage, parsed: ParsedFit) -> None:
    start = _get(frame, "start_time")
    elapsed_seconds = _get(frame, "total_elapsed_time")
    if not isinstance(start, datetime) or elapsed_seconds is None:
        return
    start = _ensure_utc(start)
    base = parsed.start_time or start
    elapsed_start = int((start - base).total_seconds())
    elapsed_end = elapsed_start + int(float(elapsed_seconds))

    parsed.laps.append(
        Lap(
            elapsed_s_start=elapsed_start,
            elapsed_s_end=elapsed_end,
            avg_heart_rate=_as_int(_get(frame, "avg_heart_rate")),
            max_heart_rate=_as_int(_get(frame, "max_heart_rate")),
            total_distance_m=_as_float(_get(frame, "total_distance")),
        ),
    )


def _read_session(frame: fitdecode.FitDataMessage, parsed: ParsedFit) -> None:
    parsed.summary = Summary(
        avg_heart_rate=_as_int(_get(frame, "avg_heart_rate")),
        max_heart_rate=_as_int(_get(frame, "max_heart_rate")),
        total_distance_m=_as_float(_get(frame, "total_distance")),
        total_calories=_as_int(_get(frame, "total_calories")),
    )
    elapsed = _get(frame, "total_elapsed_time")
    if elapsed is not None:
        parsed.duration_seconds = int(float(elapsed))


def _read_file_id(frame: fitdecode.FitDataMessage, parsed: ParsedFit) -> None:
    manufacturer = _get(frame, "manufacturer")
    product = _get(frame, "garmin_product") or _get(frame, "product")
    serial = _get(frame, "serial_number")
    if manufacturer is not None:
        parsed.device.manufacturer = str(manufacturer)
    if product is not None:
        parsed.device.product = str(product)
    if serial is not None:
        parsed.device.serial_number = str(serial)


def _read_device_info(frame: fitdecode.FitDataMessage, parsed: ParsedFit) -> None:
    if parsed.device.manufacturer is None:
        manufacturer = _get(frame, "manufacturer")
        if manufacturer is not None:
            parsed.device.manufacturer = str(manufacturer)
    if parsed.device.product is None:
        product = _get(frame, "garmin_product") or _get(frame, "product")
        if product is not None:
            parsed.device.product = str(product)


def _finalise(parsed: ParsedFit) -> None:
    if parsed.samples:
        parsed.end_time = parsed.samples[-1].t
        if not parsed.duration_seconds:
            parsed.duration_seconds = parsed.samples[-1].elapsed_s


def _get(frame: fitdecode.FitDataMessage, name: str) -> Any:
    try:
        return frame.get_value(name, fallback=None)
    except KeyError:
        return None


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
