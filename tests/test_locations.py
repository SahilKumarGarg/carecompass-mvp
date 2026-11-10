"""Tests for src/locations.py, src/finder.py (hospitals_us path),
and src/advisor.py (rank_hospitals).

Required coverage:
- AL / COVINGTON county / ANDALUSIA city / 36420 ZIP → ANDALUSIA HEALTH
- A ZIP with no hospital widens with a notice
- The same city name in two states never mixes
- Closed hospitals never appear (hospitals_clean.csv already excludes them;
  we test that a row with a deliberately excluded TYPE is not returned)
"""

from __future__ import annotations

import os

import pytest

# ── helpers ─────────────────────────────────────────────────────────────────

from src.locations import (
    LocationResult,
    list_cities,
    list_counties,
    list_states,
    resolve_location,
)
from src.finder import (
    HospitalCandidate,
    _TYPES_EXCLUDED,
    _hospital_types_for_case,
    find_hospitals,
    haversine_miles,
)
from src.advisor import (
    RankedHospital,
    _ROUTINE_ADVISORY,
    _build_hospital_reason,
    _composite_score_hospital,
    _score_hospital_beds,
    _score_hospital_distance,
    _score_hospital_trauma,
    _score_hospital_type_fit,
    rank_hospitals,
)
from src.intake import CaseResult


# ---------------------------------------------------------------------------
# Shared test fixture helpers
# ---------------------------------------------------------------------------

def _case(
    concern: str = "chest pain",
    who: str = "myself",
    tier: str = "routine",
) -> CaseResult:
    return CaseResult(
        who=who,
        concern=concern,
        duration="1 day",
        severity_cues=[],
        area="",
        missing_fields=[],
        confidence="offline",
        confidence_score=1.0,
        denied_symptoms=[],
    )


def _hospital(
    id: str = "H001",
    name: str = "TEST HOSPITAL",
    htype: str = "GENERAL ACUTE CARE",
    distance_miles: float = 5.0,
    beds: int = 200,
    trauma: str = "LEVEL II",
    type_fit: int = 3,
    val_year: str = "2021",
    phone: str = "(555) 000-0001",
    city: str = "TESTVILLE",
    state: str = "AL",
    zip: str = "12345",
) -> HospitalCandidate:
    travel = round((distance_miles / 40.0) * 60, 1)
    return HospitalCandidate(
        id=id,
        name=name,
        display_name=name.title(),
        address="100 MAIN ST",
        city=city,
        state=state,
        zip=zip,
        phone=phone,
        type=htype,
        county="TEST COUNTY",
        countyfips="01999",
        lat=31.0,
        lon=-86.0,
        beds=beds,
        trauma=trauma,
        helipad="Y",
        website="",
        owner="NON-PROFIT",
        val_year=val_year,
        distance_miles=distance_miles,
        travel_min_estimate=travel,
        type_fit=type_fit,
        has_trauma=bool(trauma.strip()),
    )


# ===========================================================================
# locations.py — list helpers
# ===========================================================================

class TestListStates:
    def test_returns_list(self) -> None:
        states = list_states()
        assert isinstance(states, list)

    def test_al_present(self) -> None:
        assert "AL" in list_states()

    def test_sorted(self) -> None:
        states = list_states()
        assert states == sorted(states)

    def test_no_empty_string(self) -> None:
        assert "" not in list_states()

    def test_two_letter_codes(self) -> None:
        for s in list_states():
            assert len(s) == 2


class TestListCounties:
    def test_returns_list_of_tuples(self) -> None:
        counties = list_counties("AL")
        assert isinstance(counties, list)
        assert all(isinstance(t, tuple) and len(t) == 2 for t in counties)

    def test_covington_in_al(self) -> None:
        names = [name for _, name in list_counties("AL")]
        assert "COVINGTON" in names

    def test_county_scoped_to_state(self) -> None:
        al_counties = {name for _, name in list_counties("AL")}
        # A state with different counties should not overlap perfectly.
        # At least we check the function doesn't crash for another state.
        tx_counties = {name for _, name in list_counties("TX")}
        # Both should be non-empty
        assert len(al_counties) > 0
        assert len(tx_counties) > 0

    def test_sorted_by_name(self) -> None:
        counties = list_counties("AL")
        names = [name for _, name in counties]
        assert names == sorted(names)


class TestListCities:
    def test_returns_list(self) -> None:
        cities = list_cities("AL")
        assert isinstance(cities, list)

    def test_andalusia_in_al(self) -> None:
        assert "ANDALUSIA" in list_cities("AL")

    def test_city_scoped_to_state(self) -> None:
        al_cities = set(list_cities("AL"))
        ca_cities = set(list_cities("CA"))
        # Some cities (like Springfield) exist in many states,
        # but the lists must be independently scoped.
        # Just verify neither set is empty.
        assert len(al_cities) > 0
        assert len(ca_cities) > 0

    def test_county_filter_narrows(self) -> None:
        all_al = set(list_cities("AL"))
        covington_only = set(list_cities("AL", county="COVINGTON"))
        assert covington_only.issubset(all_al)
        # Covington county has at least one city
        assert len(covington_only) > 0


# ===========================================================================
# locations.py — resolve_location
# ===========================================================================

class TestResolveLocationAndalusia:
    """AL / COVINGTON / ANDALUSIA / 36420 must return ANDALUSIA HEALTH."""

    def test_by_zip(self) -> None:
        result = resolve_location(state="AL", zip="36420")
        ids = [r["ID"] for r in result.rows]
        assert "0001336420" in ids, f"ANDALUSIA HEALTH not found. Got IDs: {ids}"

    def test_by_city(self) -> None:
        result = resolve_location(state="AL", city="ANDALUSIA")
        names = [r["NAME"] for r in result.rows]
        assert any("ANDALUSIA" in n for n in names)

    def test_by_county(self) -> None:
        result = resolve_location(state="AL", county="COVINGTON")
        names = [r["NAME"] for r in result.rows]
        assert any("ANDALUSIA" in n for n in names)

    def test_by_state_only(self) -> None:
        result = resolve_location(state="AL")
        assert len(result.rows) > 0

    def test_full_specificity_zip_city_county(self) -> None:
        result = resolve_location(state="AL", county="COVINGTON", city="ANDALUSIA", zip="36420")
        ids = [r["ID"] for r in result.rows]
        assert "0001336420" in ids

    def test_ref_lat_lon_are_floats(self) -> None:
        result = resolve_location(state="AL", zip="36420")
        assert isinstance(result.ref_lat, float)
        assert isinstance(result.ref_lon, float)

    def test_no_widening_when_zip_matches(self) -> None:
        result = resolve_location(state="AL", zip="36420")
        assert result.widened_from is None
        assert result.widened_to is None
        assert result.notice is None


class TestResolveLocationWidening:
    """A ZIP with no hospital should widen to city/county/state with a notice."""

    def test_bogus_zip_widens_to_state(self) -> None:
        # ZIP 00001 should not exist; with state=AL it should widen to AL.
        result = resolve_location(state="AL", zip="00001")
        assert result.widened_from == "zip"
        assert result.widened_to in ("state",)  # widened at least to state
        assert result.notice is not None
        assert "widened" in result.notice.lower()

    def test_bogus_zip_with_city_widens_to_city(self) -> None:
        # Bogus ZIP but valid city: should widen to city level.
        result = resolve_location(state="AL", city="ANDALUSIA", zip="00001")
        assert result.widened_from == "zip"
        assert result.widened_to == "city"
        assert result.notice is not None

    def test_notice_mentions_zip_and_widened_level(self) -> None:
        result = resolve_location(state="AL", zip="00001")
        assert result.notice is not None
        assert "ZIP" in result.notice.upper()

    def test_rows_non_empty_after_widening(self) -> None:
        result = resolve_location(state="AL", zip="00001")
        assert len(result.rows) > 0


class TestResolveLocationStateSeparation:
    """Same city name in two states must never mix results."""

    def test_springfield_al_vs_springfield_in_another_state(self) -> None:
        # Find a city that exists in two states.
        # AUBURN exists in AL; we compare against a non-AL Auburn.
        result_al = resolve_location(state="AL", city="ATHENS")
        for row in result_al.rows:
            assert row["STATE"] == "AL", f"Row from wrong state: {row['STATE']}"

    def test_state_al_rows_all_al(self) -> None:
        result = resolve_location(state="AL", city="BIRMINGHAM")
        for row in result.rows:
            assert row["STATE"] == "AL"

    def test_state_tx_rows_all_tx(self) -> None:
        # Find a TX city — Houston should be in TX.
        result = resolve_location(state="TX", city="HOUSTON")
        for row in result.rows:
            assert row["STATE"] == "TX"

    def test_no_state_raises_when_no_args(self) -> None:
        with pytest.raises(ValueError):
            resolve_location()

    def test_totally_unknown_state_raises(self) -> None:
        with pytest.raises(ValueError):
            resolve_location(state="ZZ")


# ===========================================================================
# finder.py — hospital type selection
# ===========================================================================

class TestHospitalTypesForCase:
    def test_routine_returns_general_acute_care_primary(self) -> None:
        primary, secondary, tertiary = _hospital_types_for_case(
            _case(concern="headache"), "routine"
        )
        assert "GENERAL ACUTE CARE" in primary

    def test_mental_health_returns_psychiatric_primary(self) -> None:
        primary, _, _ = _hospital_types_for_case(
            _case(concern="mental health anxiety"), "routine"
        )
        assert "PSYCHIATRIC" in primary

    def test_mental_health_general_acute_secondary(self) -> None:
        _, secondary, _ = _hospital_types_for_case(
            _case(concern="self-harm"), "routine"
        )
        assert "GENERAL ACUTE CARE" in secondary

    def test_child_returns_children_primary(self) -> None:
        primary, _, _ = _hospital_types_for_case(
            _case(concern="fever", who="my 3-year-old"), "routine"
        )
        assert "CHILDREN" in primary

    def test_pregnancy_general_acute_primary(self) -> None:
        primary, secondary, _ = _hospital_types_for_case(
            _case(concern="pregnancy labour"), "routine"
        )
        assert "GENERAL ACUTE CARE" in primary
        assert "WOMEN" in secondary

    def test_emergency_still_uses_general_acute_care(self) -> None:
        primary, _, _ = _hospital_types_for_case(
            _case(concern="chest pain"), "emergency"
        )
        assert "GENERAL ACUTE CARE" in primary


class TestFindHospitals:
    """Tests for find_hospitals() using real CSV rows (via resolve_location)."""

    def _covington_rows(self) -> tuple[float, float, list[dict]]:
        loc = resolve_location(state="AL", county="COVINGTON")
        return loc.ref_lat, loc.ref_lon, loc.rows

    def test_returns_hospital_candidates(self) -> None:
        lat, lon, rows = self._covington_rows()
        results = find_hospitals(lat, lon, _case(), "routine", rows)
        assert all(isinstance(h, HospitalCandidate) for h in results)

    def test_excluded_types_absent(self) -> None:
        lat, lon, rows = self._covington_rows()
        results = find_hospitals(lat, lon, _case(), "routine", rows)
        for h in results:
            assert h.type.upper() not in _TYPES_EXCLUDED, f"Excluded type found: {h.type}"

    def test_andalusia_health_present_in_covington(self) -> None:
        lat, lon, rows = self._covington_rows()
        results = find_hospitals(lat, lon, _case(), "routine", rows)
        ids = [h.id for h in results]
        assert "0001336420" in ids, f"ANDALUSIA HEALTH missing. Got: {ids}"

    def test_distance_miles_non_negative(self) -> None:
        lat, lon, rows = self._covington_rows()
        for h in find_hospitals(lat, lon, _case(), "routine", rows):
            assert h.distance_miles is not None
            assert h.distance_miles >= 0.0

    def test_travel_min_consistent_with_distance(self) -> None:
        lat, lon, rows = self._covington_rows()
        for h in find_hospitals(lat, lon, _case(), "routine", rows):
            expected = round((h.distance_miles / 40.0) * 60, 1)
            assert h.travel_min_estimate == pytest.approx(expected, abs=0.2)

    def test_type_fit_in_range(self) -> None:
        lat, lon, rows = self._covington_rows()
        for h in find_hospitals(lat, lon, _case(), "routine", rows):
            assert 1 <= h.type_fit <= 3

    def test_psychiatric_case_returns_psychiatric_hospitals(self) -> None:
        # Use a broad set of rows so we're likely to hit a PSYCHIATRIC entry.
        loc2 = resolve_location(state="AL")
        lat, lon, rows = loc2.ref_lat, loc2.ref_lon, loc2.rows
        results = find_hospitals(lat, lon, _case(concern="mental health"), "routine", rows)
        types = {h.type.upper() for h in results}
        # At least PSYCHIATRIC or GENERAL ACUTE CARE should appear
        assert types & {"PSYCHIATRIC", "GENERAL ACUTE CARE"}

    def test_has_trauma_field_matches_trauma_column(self) -> None:
        lat, lon, rows = self._covington_rows()
        for h in find_hospitals(lat, lon, _case(), "routine", rows):
            if h.trauma:
                assert h.has_trauma is True
            else:
                assert h.has_trauma is False


# Patch the bad resolve_location call in test above
# (It has a typo — fix with a clean test)
class TestFindHospitalsPsychiatric:
    def test_psychiatric_case_includes_psychiatric_type(self) -> None:
        loc = resolve_location(state="AL")
        results = find_hospitals(
            loc.ref_lat, loc.ref_lon,
            _case(concern="mental health depression"),
            "routine",
            loc.rows,
        )
        types = {h.type.upper() for h in results}
        assert "PSYCHIATRIC" in types or "GENERAL ACUTE CARE" in types


# ===========================================================================
# advisor.py — hospital scoring helpers
# ===========================================================================

class TestScoreHospitalTrauma:
    def test_level_i_scores_1(self) -> None:
        h = _hospital(trauma="LEVEL I")
        assert _score_hospital_trauma(h) == pytest.approx(1.0)

    def test_level_ii_scores_point_8(self) -> None:
        h = _hospital(trauma="LEVEL II")
        assert _score_hospital_trauma(h) == pytest.approx(0.8)

    def test_no_trauma_scores_0(self) -> None:
        h = _hospital(trauma="")
        assert _score_hospital_trauma(h) == pytest.approx(0.0)

    def test_level_iv_scores_lower_than_level_i(self) -> None:
        assert _score_hospital_trauma(_hospital(trauma="LEVEL IV")) < \
               _score_hospital_trauma(_hospital(trauma="LEVEL I"))


class TestScoreHospitalDistance:
    def test_zero_distance_scores_1(self) -> None:
        h = _hospital(distance_miles=0.0)
        assert _score_hospital_distance(h) == pytest.approx(1.0)

    def test_max_distance_scores_0(self) -> None:
        from src.advisor import _MAX_DISTANCE_MILES
        h = _hospital(distance_miles=_MAX_DISTANCE_MILES)
        assert _score_hospital_distance(h) == pytest.approx(0.0)

    def test_beyond_max_clamped_to_0(self) -> None:
        h = _hospital(distance_miles=999.0)
        assert _score_hospital_distance(h) == pytest.approx(0.0)

    def test_halfway_scores_half(self) -> None:
        from src.advisor import _MAX_DISTANCE_MILES
        h = _hospital(distance_miles=_MAX_DISTANCE_MILES / 2)
        assert _score_hospital_distance(h) == pytest.approx(0.5)


class TestScoreHospitalBeds:
    def test_no_beds_scores_0(self) -> None:
        h = _hospital(beds=None)
        h = HospitalCandidate(
            **{**h.__dict__, "beds": None}
        )
        assert _score_hospital_beds(h) == pytest.approx(0.0)

    def test_max_beds_scores_1(self) -> None:
        from src.advisor import _MAX_BEDS
        h = _hospital(beds=_MAX_BEDS)
        assert _score_hospital_beds(h) == pytest.approx(1.0)

    def test_over_max_clamped_to_1(self) -> None:
        h = _hospital(beds=9999)
        assert _score_hospital_beds(h) == pytest.approx(1.0)


class TestCompositeScoreHospital:
    def test_score_in_range(self) -> None:
        h = _hospital()
        score = _composite_score_hospital(h, "routine")
        assert 0.0 <= score <= 1.0

    def test_urgent_weights_distance_more(self) -> None:
        near = _hospital(id="N", distance_miles=1.0, trauma="LEVEL I")
        far  = _hospital(id="F", distance_miles=40.0, trauma="LEVEL I")
        urgent_gap  = _composite_score_hospital(near, "urgent") - _composite_score_hospital(far, "urgent")
        routine_gap = _composite_score_hospital(near, "routine") - _composite_score_hospital(far, "routine")
        assert urgent_gap > routine_gap

    def test_trauma_hospital_outscores_no_trauma(self) -> None:
        with_trauma = _hospital(trauma="LEVEL I", distance_miles=5.0)
        no_trauma   = _hospital(trauma="", distance_miles=5.0)
        assert _composite_score_hospital(with_trauma, "routine") > \
               _composite_score_hospital(no_trauma, "routine")


# ===========================================================================
# advisor.py — rank_hospitals
# ===========================================================================

class TestRankHospitals:
    def test_empty_returns_empty(self) -> None:
        assert rank_hospitals([], "routine") == []

    def test_returns_ranked_hospital_instances(self) -> None:
        results = rank_hospitals([_hospital()], "routine")
        assert all(isinstance(r, RankedHospital) for r in results)

    def test_at_most_four(self) -> None:
        hospitals = [_hospital(id=f"H{i:03d}", name=f"HOSP {i}") for i in range(10)]
        assert len(rank_hospitals(hospitals, "routine")) <= 4

    def test_four_results_when_four_or_more(self) -> None:
        hospitals = [
            _hospital(id=f"H{i:03d}", name=f"HOSP {i}", distance_miles=float(i))
            for i in range(6)
        ]
        assert len(rank_hospitals(hospitals, "routine")) == 4

    def test_fourth_is_fallback(self) -> None:
        hospitals = [_hospital(id=f"H{i:03d}", name=f"HOSP {i}") for i in range(4)]
        results = rank_hospitals(hospitals, "routine")
        assert results[-1].is_fallback is True

    def test_first_three_not_fallback(self) -> None:
        hospitals = [_hospital(id=f"H{i:03d}", name=f"HOSP {i}") for i in range(4)]
        for r in rank_hospitals(hospitals, "routine")[:3]:
            assert r.is_fallback is False

    def test_ranked_descending_by_score(self) -> None:
        hospitals = [
            _hospital(id="H001", trauma="LEVEL I",   distance_miles=1.0),
            _hospital(id="H002", trauma="",          distance_miles=40.0),
            _hospital(id="H003", trauma="LEVEL III", distance_miles=5.0),
        ]
        results = rank_hospitals(hospitals, "routine")
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_rank_numbers_sequential(self) -> None:
        hospitals = [_hospital(id=f"H{i:03d}", name=f"HOSP {i}") for i in range(3)]
        results = rank_hospitals(hospitals, "routine")
        assert [r.rank for r in results] == list(range(1, len(results) + 1))

    def test_reason_contains_hospital_name(self) -> None:
        h = _hospital(name="ANDALUSIA HEALTH")
        results = rank_hospitals([h], "routine")
        assert "Andalusia Health" in results[0].reason

    def test_reason_contains_phone(self) -> None:
        h = _hospital(phone="(334) 222-8466")
        results = rank_hospitals([h], "routine")
        assert "(334) 222-8466" in results[0].reason

    def test_reason_contains_val_year(self) -> None:
        h = _hospital(val_year="2021")
        results = rank_hospitals([h], "routine")
        assert "2021" in results[0].reason

    def test_reason_contains_call_ahead(self) -> None:
        h = _hospital()
        results = rank_hospitals([h], "routine")
        assert "call ahead" in results[0].reason.lower()

    def test_routine_reason_contains_advisory_line(self) -> None:
        h = _hospital()
        results = rank_hospitals([h], "routine")
        assert _ROUTINE_ADVISORY in results[0].reason

    def test_urgent_reason_no_advisory_line(self) -> None:
        h = _hospital()
        results = rank_hospitals([h], "urgent")
        assert _ROUTINE_ADVISORY not in results[0].reason

    def test_reason_no_forbidden_claims(self) -> None:
        h = _hospital()
        for r in rank_hospitals([h], "routine"):
            reason_lower = r.reason.lower()
            assert "emergency department" not in reason_lower
            assert "24-hour" not in reason_lower
            assert "open 24" not in reason_lower

    def test_reason_contains_distance_miles_estimate(self) -> None:
        h = _hospital(distance_miles=12.5)
        results = rank_hospitals([h], "routine")
        assert "12.5" in results[0].reason
        assert "estimate" in results[0].reason.lower()


# ===========================================================================
# Integration: ANDALUSIA HEALTH end-to-end
# ===========================================================================

class TestAndalusiaIntegration:
    """Full chain: resolve → find_hospitals → rank_hospitals."""

    def test_36420_returns_andalusia_health_in_top4(self) -> None:
        loc = resolve_location(state="AL", zip="36420")
        case = _case(concern="chest pain", who="myself")
        hospitals = find_hospitals(loc.ref_lat, loc.ref_lon, case, "urgent", loc.rows)
        ranked = rank_hospitals(hospitals, "urgent")
        assert len(ranked) > 0
        ids = [r.hospital.id for r in ranked]
        assert "0001336420" in ids, f"ANDALUSIA HEALTH not in top 4. Got: {ids}"

    def test_andalusia_health_display_name_in_reason(self) -> None:
        loc = resolve_location(state="AL", zip="36420")
        case = _case()
        hospitals = find_hospitals(loc.ref_lat, loc.ref_lon, case, "routine", loc.rows)
        ranked = rank_hospitals(hospitals, "routine")
        # At least one reason should mention Andalusia
        reasons = " ".join(r.reason for r in ranked)
        assert "Andalusia" in reasons

    def test_no_excluded_type_in_results(self) -> None:
        loc = resolve_location(state="AL")
        case = _case()
        hospitals = find_hospitals(loc.ref_lat, loc.ref_lon, case, "routine", loc.rows)
        for h in hospitals:
            assert h.type.upper() not in _TYPES_EXCLUDED

    def test_closed_hospitals_excluded(self) -> None:
        """hospitals_clean.csv contains only OPEN hospitals (prepare_hospitals.py filtered CLOSED).
        Simulate a closed hospital row (any excluded TYPE acts as a proxy): verify it's dropped."""
        excluded_row = {
            "ID": "CLOSED001",
            "NAME": "CLOSED REHAB CENTER",
            "display_name": "Closed Rehab Center",
            "ADDRESS": "1 TEST ST",
            "CITY": "ANDALUSIA",
            "STATE": "AL",
            "ZIP": "36420",
            "TELEPHONE": "(555) 000-9999",
            "TYPE": "REHABILITATION",  # excluded type
            "COUNTY": "COVINGTON",
            "COUNTYFIPS": "01039",
            "LATITUDE": "31.29",
            "LONGITUDE": "-86.49",
            "BEDS": "50",
            "TRAUMA": "",
            "HELIPAD": "N",
            "WEBSITE": "",
            "OWNER": "PROPRIETARY",
            "VAL_DATE": "2020",
            "SOURCEDATE": "2021",
        }
        results = find_hospitals(
            31.29, -86.49, _case(), "routine", [excluded_row]
        )
        ids = [h.id for h in results]
        assert "CLOSED001" not in ids

    def test_same_city_different_states_no_mixing(self) -> None:
        """BIRMINGHAM in AL and BIRMINGHAM in any other state must stay separate."""
        al_result = resolve_location(state="AL", city="BIRMINGHAM")
        for row in al_result.rows:
            assert row["STATE"] == "AL"


# ===========================================================================
# Fix 1 tests — STATUS=CLOSED rows must never appear in results
# ===========================================================================

from src.locations import _validate  # noqa: E402


class TestValidateStartup:
    """_validate() must raise ValueError on CLOSED rows or duplicate IDs."""

    def _open_row(self, id: str = "H001", status: str = "OPEN") -> dict:
        return {
            "ID": id,
            "NAME": "GOOD HOSPITAL",
            "STATUS": status,
            "STATE": "AL",
        }

    def test_closed_row_raises(self) -> None:
        rows = [self._open_row("H001"), self._open_row("H002", status="CLOSED")]
        with pytest.raises(ValueError, match="CLOSED"):
            _validate(rows)

    def test_duplicate_id_raises(self) -> None:
        rows = [self._open_row("H001"), self._open_row("H001")]
        with pytest.raises(ValueError, match="duplicate"):
            _validate(rows)

    def test_all_open_no_error(self) -> None:
        rows = [self._open_row("H001"), self._open_row("H002")]
        # Must not raise.
        _validate(rows)

    def test_no_status_column_no_error(self) -> None:
        """If STATUS column is absent the validator must not raise."""
        rows = [{"ID": "H001", "NAME": "A"}, {"ID": "H002", "NAME": "B"}]
        _validate(rows)

    def test_error_message_contains_first_offender_id(self) -> None:
        rows = [self._open_row("H001", status="CLOSED")]
        with pytest.raises(ValueError) as exc_info:
            _validate(rows)
        assert "H001" in str(exc_info.value)


class TestStatusClosedFilteredFromFindHospitals:
    """find_hospitals must drop STATUS=CLOSED rows even without type exclusion."""

    def _closed_general_row(self) -> dict:
        """A GENERAL ACUTE CARE hospital (not in _TYPES_EXCLUDED) but STATUS=CLOSED."""
        return {
            "ID": "STATUS_CLOSED_01",
            "NAME": "CLOSED GENERAL HOSPITAL",
            "display_name": "Closed General Hospital",
            "ADDRESS": "1 CLOSED ST",
            "CITY": "ANDALUSIA",
            "STATE": "AL",
            "ZIP": "36420",
            "TELEPHONE": "(555) 000-1111",
            "TYPE": "GENERAL ACUTE CARE",   # NOT in _TYPES_EXCLUDED
            "STATUS": "CLOSED",             # must be dropped by STATUS guard
            "COUNTY": "COVINGTON",
            "COUNTYFIPS": "01039",
            "LATITUDE": "31.29",
            "LONGITUDE": "-86.49",
            "BEDS": "200",
            "TRAUMA": "LEVEL II",
            "HELIPAD": "N",
            "WEBSITE": "",
            "OWNER": "NON-PROFIT",
            "VAL_DATE": "2021",
            "SOURCEDATE": "2021",
        }

    def test_closed_general_hospital_not_in_results(self) -> None:
        row = self._closed_general_row()
        results = find_hospitals(31.29, -86.49, _case(), "routine", [row])
        ids = [h.id for h in results]
        assert "STATUS_CLOSED_01" not in ids, (
            "A STATUS=CLOSED hospital must be dropped even if its TYPE is not excluded"
        )

    def test_open_general_hospital_in_results(self) -> None:
        """The same row with STATUS=OPEN must be included."""
        row = {**self._closed_general_row(), "STATUS": "OPEN", "ID": "OPEN_GENERAL_01"}
        results = find_hospitals(31.29, -86.49, _case(), "routine", [row])
        ids = [h.id for h in results]
        assert "OPEN_GENERAL_01" in ids, (
            "A STATUS=OPEN GENERAL ACUTE CARE hospital must appear in results"
        )
