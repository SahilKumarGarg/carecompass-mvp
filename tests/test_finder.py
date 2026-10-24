"""Tests for src/finder.py."""

from __future__ import annotations

import math

import pytest

from src.finder import (
    ServiceCandidate,
    find_services,
    haversine_km,
    map_to_service_types,
)
from src.intake import CaseResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _case(concern: str = "headache", area: str = "Greenfield", who: str = "myself") -> CaseResult:
    return CaseResult(
        who=who,
        concern=concern,
        duration="3 days",
        severity_cues=[],
        area=area,
        missing_fields=[],
        confidence="offline",
        confidence_score=1.0,
        denied_symptoms=[],
    )


# ---------------------------------------------------------------------------
# haversine_km
# ---------------------------------------------------------------------------

class TestHaversine:
    def test_same_point_is_zero(self) -> None:
        assert haversine_km(51.5, -0.1, 51.5, -0.1) == pytest.approx(0.0, abs=1e-6)

    def test_known_distance(self) -> None:
        # London to Paris ≈ 340 km
        d = haversine_km(51.5074, -0.1278, 48.8566, 2.3522)
        assert 330 < d < 350

    def test_symmetric(self) -> None:
        d1 = haversine_km(51.52, -0.13, 51.50, -0.12)
        d2 = haversine_km(51.50, -0.12, 51.52, -0.13)
        assert d1 == pytest.approx(d2, abs=1e-6)

    def test_short_urban_distance(self) -> None:
        # Two nearby points within London — should be well under 5 km
        d = haversine_km(51.5200, -0.1350, 51.5215, -0.1370)
        assert d < 1.0


# ---------------------------------------------------------------------------
# map_to_service_types
# ---------------------------------------------------------------------------

class TestMapToServiceTypes:
    def test_emergency_tier_returns_hospital_only(self) -> None:
        types = map_to_service_types(_case(), "emergency")
        assert types == ["hospital_emergency"]

    def test_routine_pharmacy_concern(self) -> None:
        types = map_to_service_types(_case(concern="repeat prescription"), "routine")
        assert types[0] == "pharmacy"

    def test_urgent_includes_hospital_first(self) -> None:
        types = map_to_service_types(_case(concern="chest breathing"), "urgent")
        assert types[0] == "hospital_emergency"

    def test_mental_health_concern_returns_specialist(self) -> None:
        types = map_to_service_types(_case(concern="mental health anxiety"), "routine")
        assert "specialist" in types

    def test_fallback_when_no_keyword_match(self) -> None:
        # Gibberish concern — should fall back to defaults
        types = map_to_service_types(_case(concern="xyzzy"), "routine")
        assert len(types) >= 1
        assert types[0] == "primary_care"

    def test_urgent_fallback_includes_hospital(self) -> None:
        types = map_to_service_types(_case(concern="xyzzy"), "urgent")
        assert types[0] == "hospital_emergency"

    def test_returns_list(self) -> None:
        assert isinstance(map_to_service_types(_case(), "routine"), list)


# ---------------------------------------------------------------------------
# find_services
# ---------------------------------------------------------------------------

class TestFindServices:
    def test_returns_list(self) -> None:
        results = find_services(_case(), "routine")
        assert isinstance(results, list)

    def test_all_candidates_in_correct_area(self) -> None:
        results = find_services(_case(area="Riverside"), "routine")
        for c in results:
            assert c.area == "Riverside"

    def test_emergency_only_returns_hospital_type(self) -> None:
        results = find_services(_case(area="Riverside"), "emergency")
        for c in results:
            assert c.type == "hospital_emergency"

    def test_unknown_area_returns_empty(self) -> None:
        results = find_services(_case(area="Atlantis"), "routine")
        assert results == []

    def test_candidates_are_service_candidate_instances(self) -> None:
        results = find_services(_case(), "routine")
        for c in results:
            assert isinstance(c, ServiceCandidate)

    def test_distance_km_is_float_for_physical_services(self) -> None:
        results = find_services(_case(area="Greenfield", concern="prescription pharmacy"), "routine")
        physical = [c for c in results if c.type != "telemedicine"]
        for c in physical:
            assert c.distance_km is not None
            assert isinstance(c.distance_km, float)

    def test_telemedicine_has_no_distance(self) -> None:
        results = find_services(_case(area="Greenfield", concern="online video remote"), "routine")
        tele = [c for c in results if c.type == "telemedicine"]
        for c in tele:
            assert c.distance_km is None
            assert c.travel_min_estimate is None

    def test_travel_estimate_positive(self) -> None:
        results = find_services(_case(area="Riverside", concern="prescription pharmacy"), "routine")
        physical = [c for c in results if c.distance_km is not None]
        for c in physical:
            assert c.travel_min_estimate is not None
            assert c.travel_min_estimate >= 0

    def test_fit_score_in_range(self) -> None:
        results = find_services(_case(), "routine")
        for c in results:
            assert 1 <= c.fit_score <= 3  # 0 would mean it was filtered out

    def test_service_fields_present(self) -> None:
        results = find_services(_case(area="Northgate"), "routine")
        assert len(results) > 0
        c = results[0]
        assert c.id.startswith("SVC")
        assert c.name
        assert c.phone
        assert c.last_verified

    def test_travel_consistent_with_distance(self) -> None:
        """travel_min ≈ distance_km / 25 * 60."""
        results = find_services(_case(area="Eastfield"), "routine")
        physical = [c for c in results if c.distance_km is not None]
        for c in physical:
            expected = (c.distance_km / 25.0) * 60
            assert c.travel_min_estimate == pytest.approx(expected, abs=0.2)
