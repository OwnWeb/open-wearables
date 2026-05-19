"""Tests for canonical FIT field name resolution + unit conversion."""

from app.services.fit.field_map import (
    CANONICAL_FIELDS,
    LATLNG_FIELD,
    LATLNG_FIT_NAMES,
    canonical_to_fit_names,
    semicircle_to_degree,
)


class TestCanonicalToFitNames:
    def test_returns_single_fit_name_for_heart_rate(self) -> None:
        assert canonical_to_fit_names("heart_rate") == ("heart_rate",)

    def test_returns_enhanced_variant_first_for_speed(self) -> None:
        assert canonical_to_fit_names("speed") == ("enhanced_speed", "speed")

    def test_returns_empty_tuple_for_unknown_field(self) -> None:
        assert canonical_to_fit_names("unknown_field") == ()

    def test_latlng_returns_position_fields(self) -> None:
        assert canonical_to_fit_names(LATLNG_FIELD) == LATLNG_FIT_NAMES

    def test_every_canonical_key_has_at_least_one_fit_name(self) -> None:
        for canonical in CANONICAL_FIELDS:
            assert canonical_to_fit_names(canonical) == CANONICAL_FIELDS[canonical]
            assert len(canonical_to_fit_names(canonical)) >= 1


class TestSemicircleToDegree:
    def test_zero_maps_to_zero(self) -> None:
        assert semicircle_to_degree(0) == 0.0

    def test_full_positive_swing_maps_to_180(self) -> None:
        assert semicircle_to_degree(2**31) == 180.0

    def test_full_negative_swing_maps_to_minus_180(self) -> None:
        assert semicircle_to_degree(-(2**31)) == -180.0

    def test_half_swing_maps_to_ninety_degrees(self) -> None:
        assert semicircle_to_degree(-(2**30)) == -90.0
