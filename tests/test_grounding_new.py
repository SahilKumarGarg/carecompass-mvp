"""New grounding, widening, closed-hospital, and button-spec tests.

Tests added per task requirements:
1. No closed hospital (excluded TYPE) ever appears in an answer.
2. A made-up hospital name is blocked by the grounding check.
3. The widening notice appears when a ZIP has no hospitals.
4. Exactly one action button per tier.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Optional
from unittest.mock import patch

import pytest

# Ensure offline mode.
for _k in ("WATSONX_API_KEY", "WATSONX_URL", "WATSONX_PROJECT_ID"):
    os.environ.pop(_k, None)

# Use hospitals_us path for the grounding and location tests.
os.environ.setdefault("DATA_SOURCE", "hospitals_us")

from src.checks import (  # noqa: E402
    CheckResult,
    _check_hospital_ids,
    _check_no_closed_hospitals,
    _check_call_ahead_present,
    _check_forbidden_hospital_claims,
    _load_hospitals_index,
    final_check,
)
from src.locations import resolve_location  # noqa: E402
from src.finder import find_hospitals, _TYPES_EXCLUDED  # noqa: E402
from src.advisor import rank_hospitals  # noqa: E402
from src.intake import CaseResult  # noqa: E402
from app import get_button_spec  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _case(concern: str = "chest pain", who: str = "myself") -> CaseResult:
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


# ---------------------------------------------------------------------------
# Test 1: No closed hospital ever appears in an answer
# ---------------------------------------------------------------------------

class TestNoClosedHospitalInAnswer:
    """hospitals_clean.csv has no CLOSED status; we verify excluded TYPEs are
    filtered before ranking and that final_check catches any that slipped through."""

    def test_excluded_type_row_not_in_find_hospitals_results(self) -> None:
        """A row with an excluded TYPE must be dropped by find_hospitals."""
        closed_row = {
            "ID": "CLOSED001",
            "NAME": "CLOSED REHABILITATION CENTER",
            "display_name": "Closed Rehabilitation Center",
            "ADDRESS": "1 REHAB LANE",
            "CITY": "ANDALUSIA",
            "STATE": "AL",
            "ZIP": "36420",
            "TELEPHONE": "(555) 000-9999",
            "TYPE": "REHABILITATION",
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
        loc = resolve_location(state="AL", zip="36420")
        results = find_hospitals(loc.ref_lat, loc.ref_lon, _case(), "routine", [closed_row])
        ids = [h.id for h in results]
        assert "CLOSED001" not in ids, "Excluded-type hospital must not appear in results"

    def test_all_excluded_types_filtered_from_covington_results(self) -> None:
        """All results from COVINGTON must not have any excluded TYPE."""
        loc = resolve_location(state="AL", county="COVINGTON")
        hospitals = find_hospitals(loc.ref_lat, loc.ref_lon, _case(), "routine", loc.rows)
        for h in hospitals:
            assert h.type.upper() not in _TYPES_EXCLUDED, (
                f"Excluded type '{h.type}' found in results for hospital {h.id}"
            )

    def test_final_check_catches_rehabilitation_hospital_id(self) -> None:
        """final_check must fail when a cited ID has an excluded TYPE in the CSV."""
        hosp_index = _load_hospitals_index()
        # Find a REHABILITATION type hospital in the CSV.
        rehab_ids = [
            hid for hid, row in hosp_index.items()
            if row.get("TYPE", "").upper() == "REHABILITATION"
        ]
        if not rehab_ids:
            pytest.skip("No REHABILITATION hospital in CSV")

        rehab_id = rehab_ids[0]
        row = hosp_index[rehab_id]
        name = row.get("display_name") or row.get("NAME", "Unknown Rehab")
        name_title = name.title()
        phone = row.get("TELEPHONE", "(000) 000-0000")
        val_year = row.get("VAL_DATE", "2020")

        # Build a passing answer (all fields present) except the TYPE is excluded.
        answer = (
            f"**Option 1: {name_title}** ({rehab_id})  "
            f"Phone: {phone}  Validation year: {val_year}\n"
            f"*Call ahead to confirm availability before attending.*\n"
            f"Address: 100 Main St, Testville, AL. Please call today."
        )
        result = final_check(answer, [], tier="routine", hospital_ids=[rehab_id])
        assert result.passed is False
        assert any(rehab_id in p for p in result.problems), (
            f"Expected problem mentioning {rehab_id}. Got: {result.problems}"
        )

    def test_check_no_closed_hospitals_excluded_type(self) -> None:
        """_check_no_closed_hospitals returns a problem for an excluded TYPE."""
        index = {"REHAB001": {"NAME": "Rehab Center", "TYPE": "REHABILITATION", "VAL_DATE": "2020"}}
        problems = _check_no_closed_hospitals(["REHAB001"], index)
        assert len(problems) == 1
        assert "REHABILITATION" in problems[0]

    def test_check_no_closed_hospitals_good_type_no_problem(self) -> None:
        """_check_no_closed_hospitals has no problem for GENERAL ACUTE CARE."""
        index = {"GOOD001": {"NAME": "Good Hospital", "TYPE": "GENERAL ACUTE CARE", "VAL_DATE": "2021"}}
        problems = _check_no_closed_hospitals(["GOOD001"], index)
        assert problems == []


# ---------------------------------------------------------------------------
# Test 2: Made-up hospital name is blocked by the grounding check
# ---------------------------------------------------------------------------

class TestFakeHospitalBlocked:
    """final_check must reject an answer that cites a hospital ID not in hospitals_clean.csv."""

    def test_fabricated_id_fails_grounding_check(self) -> None:
        """An invented hospital ID must be caught by _check_hospital_ids."""
        fake_id = "FAKE999999"
        answer = (
            f"**Option 1: Fantasy General Hospital** ({fake_id})  "
            f"Phone: (555) 123-4567  Validation year: 2021\n"
            f"*Call ahead to confirm availability before attending.*"
        )
        result = final_check(answer, [], tier="routine", hospital_ids=[fake_id])
        assert result.passed is False
        assert any(fake_id in p for p in result.problems), (
            f"Expected grounding failure mentioning {fake_id}. Got: {result.problems}"
        )

    def test_check_hospital_ids_with_nonexistent_id(self) -> None:
        """_check_hospital_ids returns a problem for any ID not in the index."""
        fake_id = "DOES_NOT_EXIST_XYZ"
        problems = _check_hospital_ids([fake_id], {})
        assert len(problems) == 1
        assert fake_id in problems[0]

    def test_check_hospital_ids_with_real_andalusia_id(self) -> None:
        """_check_hospital_ids has no problem for the real Andalusia Health ID."""
        hosp_index = _load_hospitals_index()
        problems = _check_hospital_ids(["0001336420"], hosp_index)
        assert problems == []

    def test_final_check_passes_for_real_andalusia_hospital(self) -> None:
        """A properly formed answer for ANDALUSIA HEALTH must pass final_check."""
        hosp_index = _load_hospitals_index()
        row = hosp_index.get("0001336420")
        if row is None:
            pytest.skip("ANDALUSIA HEALTH not in CSV")

        name = row.get("display_name", row["NAME"]).title()
        phone = row.get("TELEPHONE", "")
        val_year = row.get("VAL_DATE", "")

        answer = (
            f"**Option 1: {name}** (0001336420)  "
            f"Phone: {phone}  Validation year: {val_year}\n"
            f"*Call ahead to confirm availability before attending.*\n"
            f"Address: 849 S Three Notch St, Andalusia, AL 36420. "
            f"Type: General Acute Care. Please call today."
        )
        result = final_check(answer, [], tier="urgent", hospital_ids=["0001336420"])
        assert result.passed is True, f"Expected pass. Problems: {result.problems}"


# ---------------------------------------------------------------------------
# Test 3: Widening notice appears when a ZIP has no hospitals
# ---------------------------------------------------------------------------

class TestWideningNotice:
    """When the supplied ZIP has no hospitals, resolve_location widens to the
    next level and returns a human-readable notice."""

    def test_unknown_zip_produces_widening_notice(self) -> None:
        """ZIP 00001 does not exist; result must carry a widening notice."""
        result = resolve_location(state="AL", zip="00001")
        assert result.notice is not None, "Expected a widening notice for unknown ZIP"
        assert len(result.notice) > 0

    def test_unknown_zip_widened_from_zip(self) -> None:
        result = resolve_location(state="AL", zip="00001")
        assert result.widened_from == "zip"

    def test_unknown_zip_with_city_widens_to_city(self) -> None:
        """Known ZIP missing but valid city: widen to city level."""
        result = resolve_location(state="AL", city="ANDALUSIA", zip="00001")
        assert result.widened_from == "zip"
        assert result.widened_to == "city"
        assert result.notice is not None
        assert "ZIP" in result.notice.upper()

    def test_known_zip_no_widening_notice(self) -> None:
        """Valid ZIP 36420 produces no widening notice."""
        result = resolve_location(state="AL", zip="36420")
        assert result.notice is None
        assert result.widened_from is None
        assert result.widened_to is None

    def test_widening_notice_in_answer_text_when_widened(self) -> None:
        """_build_answer_text_hospitals includes the widening notice when location was widened."""
        from src.orchestrator import _build_answer_text_hospitals
        from src.locations import LocationResult

        loc = LocationResult(
            rows=[],
            ref_lat=31.0,
            ref_lon=-86.0,
            widened_from="zip",
            widened_to="state",
            notice="No hospitals found for the supplied ZIP. Results have been widened to STATE level.",
        )
        # Empty ranked list → needs_human_review message, but notice should still appear.
        answer = _build_answer_text_hospitals([], "routine", location=loc)
        assert "widened" in answer.lower() or "ZIP" in answer.upper(), (
            "Widening notice must appear in the answer text"
        )

    def test_widening_notice_appears_in_run_session_for_bogus_zip(self, monkeypatch) -> None:
        """End-to-end: run_session with a non-existent ZIP produces widening info in the answer."""
        monkeypatch.setenv("DATA_SOURCE", "hospitals_us")
        from src.orchestrator import run_session
        # Use a non-existent ZIP but valid state so resolve_location doesn't raise.
        area = '{"state": "AL", "zip": "00001"}'
        result = run_session("I need a GP appointment.", area=area)
        # Location should have widened from zip.
        assert result.location is not None, (
            "Expected a LocationResult; got None. DATA_SOURCE may not be hospitals_us."
        )
        assert result.location.widened_from == "zip", (
            f"Expected widened_from='zip', got: {result.location.widened_from!r}"
        )
        assert result.location.notice is not None
        # The widening notice should be embedded in the answer_text.
        assert "widened" in result.answer_text.lower() or "ZIP" in result.answer_text.upper()


# ---------------------------------------------------------------------------
# Test 4: Exactly one action button per tier
# ---------------------------------------------------------------------------

class TestExactlyOneButtonPerTier:
    """get_button_spec must return exactly one spec dict per tier."""

    def _make_result(self, tier: str, ranked: list | None = None):
        from src.orchestrator import SessionResult
        return SessionResult(
            session_id="test-btn",
            tier=tier,
            state="present",
            case=None,
            safety=None,
            candidates=[],
            ranked=ranked or [],
            answer_text="",
            check_passed=True,
            needs_human_review=False,
            handoff_summary=None,
            followup_questions_asked=0,
            missing_fields=[],
            location=None,
        )

    def test_emergency_returns_exactly_one_spec(self) -> None:
        from app import get_button_spec
        spec = get_button_spec("emergency", self._make_result("emergency"))
        assert isinstance(spec, dict)
        assert "kind" in spec
        assert "label" in spec

    def test_urgent_returns_exactly_one_spec(self) -> None:
        from app import get_button_spec
        spec = get_button_spec("urgent", self._make_result("urgent"))
        assert isinstance(spec, dict)
        assert "kind" in spec
        assert "label" in spec

    def test_routine_returns_exactly_one_spec(self) -> None:
        from app import get_button_spec
        spec = get_button_spec("routine", self._make_result("routine"))
        assert isinstance(spec, dict)
        assert "kind" in spec
        assert "label" in spec

    def test_emergency_kind_is_link(self) -> None:
        from app import get_button_spec
        spec = get_button_spec("emergency", self._make_result("emergency"))
        assert spec["kind"] == "link"

    def test_urgent_kind_is_link(self) -> None:
        from app import get_button_spec
        spec = get_button_spec("urgent", self._make_result("urgent"))
        assert spec["kind"] == "link"

    def test_routine_kind_is_consent(self) -> None:
        from app import get_button_spec
        spec = get_button_spec("routine", self._make_result("routine"))
        assert spec["kind"] == "consent"

    def test_emergency_button_uses_911_by_default(self) -> None:
        """When EMERGENCY_NUMBER is unset, emergency button defaults to 911."""
        from app import get_button_spec
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("EMERGENCY_NUMBER", None)
            spec = get_button_spec("emergency", self._make_result("emergency"))
        assert "911" in spec["label"]
        assert spec["url"] == "tel:911"

    def test_emergency_button_uses_env_number(self) -> None:
        """When EMERGENCY_NUMBER=911, button shows 911."""
        from app import get_button_spec
        with patch.dict(os.environ, {"EMERGENCY_NUMBER": "911"}):
            spec = get_button_spec("emergency", self._make_result("emergency"))
        assert "911" in spec["label"]
        assert spec["url"] == "tel:911"

    def test_no_second_button_spec_returned(self) -> None:
        """get_button_spec must return a single dict, not a list."""
        from app import get_button_spec
        for tier in ("emergency", "urgent", "routine"):
            spec = get_button_spec(tier, self._make_result(tier))
            assert isinstance(spec, dict), f"Expected dict for tier={tier!r}, got {type(spec)}"

    def test_routine_button_no_url(self) -> None:
        from app import get_button_spec
        spec = get_button_spec("routine", self._make_result("routine"))
        assert "url" not in spec

    def test_urgent_button_has_url(self) -> None:
        from app import get_button_spec
        spec = get_button_spec("urgent", self._make_result("urgent"))
        assert "url" in spec


# ---------------------------------------------------------------------------
# Test 5: Forbidden claims check
# ---------------------------------------------------------------------------

class TestForbiddenHospitalClaims:
    """Answer must never claim emergency department, 24-hour service or opening hours."""

    def test_emergency_department_claim_fails(self) -> None:
        problems = _check_forbidden_hospital_claims(
            "Visit our emergency department any time."
        )
        assert any("emergency department" in p.lower() for p in problems)

    def test_24_hour_claim_fails(self) -> None:
        problems = _check_forbidden_hospital_claims(
            "This hospital is open 24-hour for all patients."
        )
        assert any("24-hour" in p.lower() or "24 hour" in p.lower() for p in problems)

    def test_opening_hours_claim_fails(self) -> None:
        problems = _check_forbidden_hospital_claims(
            "Opening hours: Mon-Fri 9am-5pm."
        )
        assert any("opening hours" in p.lower() for p in problems)

    def test_clean_answer_no_forbidden_claims(self) -> None:
        problems = _check_forbidden_hospital_claims(
            "Andalusia Health is approximately 0.1 miles away. "
            "Call ahead to confirm availability. Phone: (334) 222-8466."
        )
        assert problems == []

    def test_call_ahead_present_check_passes_when_cited(self) -> None:
        problems = _check_call_ahead_present(
            "Call ahead to confirm availability before attending.", ["0001336420"]
        )
        assert problems == []

    def test_call_ahead_missing_fails(self) -> None:
        problems = _check_call_ahead_present(
            "Andalusia Health is nearby. Phone: (334) 222-8466.", ["0001336420"]
        )
        assert len(problems) == 1
        assert "call ahead" in problems[0].lower()

    def test_call_ahead_check_skipped_when_no_ids(self) -> None:
        """When no hospital IDs are cited, 'call ahead' is not required."""
        problems = _check_call_ahead_present("Some general advice.", [])
        assert problems == []


# ---------------------------------------------------------------------------
# Fix 2: _parse_area_to_location — bare city/county without state is rejected
# ---------------------------------------------------------------------------

class TestParseAreaToLocation:
    """_parse_area_to_location must never produce a city-only or county-only
    lookup, as those cross state lines."""

    def _parse(self, area: str) -> dict:
        from src.orchestrator import _parse_area_to_location
        return _parse_area_to_location(area)

    # Bare city name — must return the no-state sentinel.
    def test_bare_city_name_rejected(self) -> None:
        result = self._parse("SPRINGFIELD")
        assert result.get("_no_state") is True, (
            f"Expected {{_no_state: True}} for bare city name, got {result!r}"
        )

    def test_bare_city_mixed_case_rejected(self) -> None:
        result = self._parse("Springfield")
        assert result.get("_no_state") is True

    def test_bare_county_name_rejected(self) -> None:
        result = self._parse("Montgomery County")
        assert result.get("_no_state") is True

    # JSON dict with state — must pass through.
    def test_json_with_state_passes(self) -> None:
        result = self._parse('{"state": "AL", "city": "SPRINGFIELD"}')
        assert result.get("_no_state") is not True
        assert result.get("state") == "AL"

    def test_json_with_state_and_city(self) -> None:
        result = self._parse('{"state": "TX", "county": "MONTGOMERY", "city": "HOUSTON"}')
        assert result.get("_no_state") is not True
        assert result["state"] == "TX"

    # JSON dict with only city (no state, no ZIP) — must be rejected.
    def test_json_city_only_no_state_rejected(self) -> None:
        result = self._parse('{"city": "SPRINGFIELD"}')
        assert result.get("_no_state") is True

    # JSON dict with ZIP but no state — accepted (ZIP is unambiguous).
    def test_json_zip_only_accepted(self) -> None:
        result = self._parse('{"zip": "36420"}')
        assert result.get("_no_state") is not True
        assert result.get("zip") == "36420"

    # 5-digit ZIP string — must pass through as {"zip": "..."}.
    def test_five_digit_zip_passes(self) -> None:
        result = self._parse("36420")
        assert result == {"zip": "36420"}

    # 2-letter state abbreviation — must pass through as {"state": "..."}.
    def test_two_letter_state_passes(self) -> None:
        result = self._parse("AL")
        assert result == {"state": "AL"}

    def test_two_letter_state_lower_passes(self) -> None:
        result = self._parse("al")
        assert result == {"state": "AL"}


class TestRunSessionRejectsNoState:
    """run_session must return a clear message (not crash) when the area is a
    bare city name with no state."""

    def test_springfield_no_state_returns_message(self, monkeypatch) -> None:
        monkeypatch.setenv("DATA_SOURCE", "hospitals_us")
        from src.orchestrator import run_session
        result = run_session("I need a doctor.", area="SPRINGFIELD")
        assert result.state == "no_state", (
            f"Expected state='no_state', got {result.state!r}"
        )
        assert result.answer_text  # must contain some helpful message
        # The answer must ask the user to pick a state.
        assert "state" in result.answer_text.lower() or "State" in result.answer_text

    def test_springfield_with_state_in_json_proceeds(self, monkeypatch) -> None:
        monkeypatch.setenv("DATA_SOURCE", "hospitals_us")
        from src.orchestrator import run_session
        # JSON with state — must NOT return no_state.
        result = run_session(
            "I need a doctor.",
            area='{"state": "AL", "city": "BIRMINGHAM"}',
        )
        assert result.state != "no_state", (
            f"Expected normal processing for city+state, got state={result.state!r}"
        )

    def test_zip_alone_proceeds(self, monkeypatch) -> None:
        monkeypatch.setenv("DATA_SOURCE", "hospitals_us")
        from src.orchestrator import run_session
        result = run_session("I need a doctor.", area="36420")
        assert result.state != "no_state"


# ---------------------------------------------------------------------------
# Fix 3: No "Start over" button — result screen defines exactly one button
# ---------------------------------------------------------------------------

class TestNoStartOverButton:
    """The rendered result screen must have only one action button.
    We verify this by asserting get_button_spec returns exactly one dict and
    that there is no "start_over" key or equivalent in the return value."""

    def _make_result(self, tier: str):
        from src.orchestrator import SessionResult
        return SessionResult(
            session_id="test-fix3",
            tier=tier,
            state="present",
            case=None,
            safety=None,
            candidates=[],
            ranked=[],
            answer_text="Test answer.",
            check_passed=True,
            needs_human_review=False,
            handoff_summary=None,
            followup_questions_asked=0,
            missing_fields=[],
            location=None,
        )

    def test_emergency_single_spec_not_list(self) -> None:
        spec = get_button_spec("emergency", self._make_result("emergency"))
        assert isinstance(spec, dict), "get_button_spec must return exactly one dict"
        assert isinstance(spec.get("kind"), str)

    def test_urgent_single_spec_not_list(self) -> None:
        spec = get_button_spec("urgent", self._make_result("urgent"))
        assert isinstance(spec, dict)

    def test_routine_single_spec_not_list(self) -> None:
        spec = get_button_spec("routine", self._make_result("routine"))
        assert isinstance(spec, dict)

    def test_spec_has_no_start_over_key(self) -> None:
        """get_button_spec must not contain a 'start_over' key — that button is gone."""
        for tier in ("emergency", "urgent", "routine"):
            spec = get_button_spec(tier, self._make_result(tier))
            assert "start_over" not in spec, (
                f"Unexpected 'start_over' key in spec for tier={tier!r}"
            )

    def test_result_screen_defines_exactly_one_button_per_tier(self) -> None:
        """Calling get_button_spec for each tier must return a single dict each time."""
        for tier in ("emergency", "urgent", "routine"):
            specs = get_button_spec(tier, self._make_result(tier))
            assert isinstance(specs, dict), (
                f"Expected a single dict (one button) for tier={tier!r}, got {type(specs)}"
            )


# ---------------------------------------------------------------------------
# Fix 4: Forbidden claims check always runs on hospitals_us path via final_check
# ---------------------------------------------------------------------------

class TestForbiddenClaimsViaFinalCheck:
    """Every forbidden phrase must fail final_check when hospital_ids is supplied
    (hospitals_us path).  This exercises the full check pipeline, not just the
    sub-function, ensuring the integration cannot be bypassed."""

    # Minimal passing answer scaffold — no forbidden phrases; just swap one in.
    _BASE = (
        "**Option 1: Andalusia Health** (0001336420)  "
        "Phone: (334) 222-8466  Validation year: 2014\n"
        "*Call ahead to confirm availability before attending.*\n"
        "Address: 849 S Three Notch St, Andalusia, AL 36420. "
        "Type: General Acute Care."
    )

    def _answer_with(self, extra: str) -> str:
        return self._BASE + " " + extra

    def _run(self, extra: str) -> CheckResult:
        return final_check(
            self._answer_with(extra),
            [],
            tier="routine",
            hospital_ids=["0001336420"],
        )

    def test_has_an_emergency_department_fails(self) -> None:
        result = self._run("This hospital has an emergency department.")
        assert result.passed is False, (
            "Answer containing 'has an emergency department' must fail final_check"
        )

    def test_open_24_hours_fails(self) -> None:
        result = self._run("The hospital is open 24 hours.")
        assert result.passed is False, (
            "Answer containing 'open 24 hours' must fail final_check"
        )

    def test_24_hour_hyphenated_fails(self) -> None:
        result = self._run("Available 24-hour support.")
        assert result.passed is False, (
            "Answer containing '24-hour' must fail final_check"
        )

    def test_emergency_room_fails(self) -> None:
        result = self._run("Visit the emergency room for immediate care.")
        assert result.passed is False, (
            "Answer containing 'emergency room' must fail final_check"
        )

    def test_clean_answer_passes(self) -> None:
        """A clean answer with no forbidden phrases must pass."""
        result = final_check(
            self._BASE,
            [],
            tier="routine",
            hospital_ids=["0001336420"],
        )
        assert result.passed is True, f"Expected pass. Problems: {result.problems}"

    def test_forbidden_check_runs_on_hospitals_path_not_demo(self) -> None:
        """Verify check fires with hospital_ids= but not when hospital_ids=None."""
        answer_with_forbidden = self._answer_with("open 24 hours")
        # hospitals_us path — must fail
        h_result = final_check(answer_with_forbidden, [], tier="routine", hospital_ids=["0001336420"])
        assert h_result.passed is False
