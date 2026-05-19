from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Device(BaseModel):
    manufacturer: str | None = None
    product: str | None = None
    serial_number: str | None = None


class Sample(BaseModel):
    model_config = ConfigDict(extra="allow")

    t: str = Field(description="ISO-8601 UTC timestamp of the FIT record")
    elapsed_s: int = Field(description="Seconds since the first record")


class Lap(BaseModel):
    elapsed_s_start: int
    elapsed_s_end: int
    avg_heart_rate: int | None = None
    max_heart_rate: int | None = None
    total_distance_m: float | None = None


class Summary(BaseModel):
    avg_heart_rate: int | None = None
    max_heart_rate: int | None = None
    total_distance_m: float | None = None
    total_calories: int | None = None


class WorkoutExportResponse(BaseModel):
    workout_key: str
    provider: str
    user_id: UUID
    start_time: str | None = None
    end_time: str | None = None
    duration_seconds: int
    device: Device
    fields: list[str]
    samples: list[dict[str, Any]]
    laps: list[Lap]
    summary: Summary
