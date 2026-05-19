"""Canonical FIT field names exposed by the workout export endpoint."""

from __future__ import annotations

SEMICIRCLE_TO_DEGREE = 180.0 / (2**31)

# Canonical key -> ordered list of FIT record field names to try.
# `enhanced_*` variants supersede the legacy fields on newer FITs; we read either.
CANONICAL_FIELDS: dict[str, tuple[str, ...]] = {
    "heart_rate": ("heart_rate",),
    "speed": ("enhanced_speed", "speed"),
    "cadence": ("cadence",),
    "power": ("power",),
    "altitude": ("enhanced_altitude", "altitude"),
    "distance": ("distance",),
    "temperature": ("temperature",),
}

# Opt-in only. Position fields are FIT semicircles; converted to degrees in the parser.
LATLNG_FIELD = "latlng"
LATLNG_FIT_NAMES: tuple[str, str] = ("position_lat", "position_long")

DEFAULT_FIELDS: tuple[str, ...] = ("heart_rate",)


def canonical_to_fit_names(canonical: str) -> tuple[str, ...]:
    if canonical == LATLNG_FIELD:
        return LATLNG_FIT_NAMES
    return CANONICAL_FIELDS.get(canonical, ())


def semicircle_to_degree(value: int) -> float:
    return value * SEMICIRCLE_TO_DEGREE
