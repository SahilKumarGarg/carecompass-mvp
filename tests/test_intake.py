"""Tests for src/intake.py — offline fallback only (no watsonx.ai credentials needed)."""

from __future__ import annotations

import os
import pytest

# Force offline mode for the entire test session by clearing watsonx env vars.
for _k in ("WATSONX_API_KEY", "WATSONX_URL", "WATSONX_PROJECT_ID"):
    os.environ.pop(_k, None)

from src.intake import (  # noqa: E402  (import after env manipulation)
    CaseResult,
    _offline_extract,
    _watsonx_available,
    extract_case,
    get_followup_question,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _call(text: str, area: str = "Greenfield") -> CaseResult:
    """Convenience wrapper that always calls the offline path."""
    return _offline_extract(text, area)


# ---------------------------------------------------------------------------
# Schema / type tests
# ---------------------------------------------------------------------------

class TestCaseResultSchema:
    def test_returns_case_result(self) -> None:
        result = _call("I have a headache.")
        assert isinstance(result, CaseResult)

    def test_area_passed_through(self) -> None:
        result = _call("I have a headache.", area="Riverside")
        assert result.area == "Riverside"

    def test_confidence_is_offline(self) -> None:
        result = _call("I have a headache.")
        assert result.confidence == "offline"

    def test_confidence_score_in_range(self) -> None:
        result = _call("I have a headache.")
        assert 0.0 <= result.confidence_score <= 1.0

    def test_severity_cues_is_list(self) -> None:
        result = _call("I have a headache.")
        assert isinstance(result.severity_cues, list)

    def test_missing_fields_is_list(self) -> None:
        result = _call("I have a headache.")
        assert isinstance(result.missing_fields, list)


# ---------------------------------------------------------------------------
# Who extraction
# ---------------------------------------------------------------------------

class TestWhoExtraction:
    def test_self_first_person(self) -> None:
        result = _call("I have been feeling unwell since this morning.")
        assert result.who == "myself"

    def test_relation_dad(self) -> None:
        result = _call("My dad is clutching his chest.")
        assert result.who == "my dad"

    def test_relation_mum(self) -> None:
        result = _call("My mum has a high temperature.")
        assert result.who == "my mum"

    def test_relation_partner(self) -> None:
        result = _call("My partner has a very high temperature and is confused.")
        assert result.who == "my partner"

    def test_age_description(self) -> None:
        result = _call("My 4-year-old has been crying all day.")
        assert "4" in result.who

    def test_empty_when_no_who(self) -> None:
        result = _call("There is chest pain and sweating.")
        # no clear subject → missing
        assert "who" in result.missing_fields


# ---------------------------------------------------------------------------
# Duration extraction
# ---------------------------------------------------------------------------

class TestDurationExtraction:
    def test_minutes_ago(self) -> None:
        result = _call("This started about 10 minutes ago.")
        assert result.duration != ""

    def test_days(self) -> None:
        result = _call("I've had this for 3 days.")
        assert "3" in result.duration or "day" in result.duration.lower()

    def test_since_yesterday(self) -> None:
        result = _call("I've had a headache since yesterday.")
        assert "yesterday" in result.duration.lower()

    def test_this_morning(self) -> None:
        result = _call("It started this morning.")
        assert result.duration != ""

    def test_suddenly(self) -> None:
        result = _call("It came on suddenly.")
        assert result.duration != ""

    def test_missing_when_no_duration(self) -> None:
        result = _call("I have a rash on my arm.")
        assert "duration" in result.missing_fields


# ---------------------------------------------------------------------------
# Severity cue extraction
# ---------------------------------------------------------------------------

class TestSeverityCueExtraction:
    def test_severe_detected(self) -> None:
        result = _call("I have severe chest pain.")
        assert any("severe" in c for c in result.severity_cues)

    def test_mild_detected(self) -> None:
        result = _call("I have a mild headache.")
        assert any("mild" in c for c in result.severity_cues)

    def test_suddenly_detected(self) -> None:
        result = _call("It came on suddenly.")
        assert any("suddenly" in c for c in result.severity_cues)

    def test_no_severity_cues_empty_list(self) -> None:
        result = _call("I need a repeat prescription.")
        assert result.severity_cues == []


# ---------------------------------------------------------------------------
# Missing fields
# ---------------------------------------------------------------------------

class TestMissingFields:
    def test_complete_input_has_no_missing_fields(self) -> None:
        # who=myself, duration=3 days, concern present
        result = _call("I have had a rash on my forearm for 3 days.")
        # At minimum, 'who' and 'duration' should be found
        assert "who" not in result.missing_fields
        assert "duration" not in result.missing_fields

    def test_missing_duration_reported(self) -> None:
        result = _call("I have a rash on my arm.")
        assert "duration" in result.missing_fields

    def test_confidence_score_lower_when_fields_missing(self) -> None:
        full = _call("I have had a rash on my arm for 3 days.")
        partial = _call("There is a rash.")
        assert full.confidence_score >= partial.confidence_score


# ---------------------------------------------------------------------------
# extract_case public function — offline path
# ---------------------------------------------------------------------------

class TestExtractCase:
    def test_returns_case_result(self) -> None:
        result = extract_case("I have a headache.", "Northgate")
        assert isinstance(result, CaseResult)

    def test_uses_offline_when_no_env_vars(self) -> None:
        result = extract_case("I have a headache.", "Westbury")
        assert result.confidence == "offline"

    def test_area_preserved(self) -> None:
        result = extract_case("My mum is unwell.", "Eastfield")
        assert result.area == "Eastfield"


# ---------------------------------------------------------------------------
# watsonx_available helper
# ---------------------------------------------------------------------------

class TestWatsonxAvailable:
    def test_false_when_no_env_vars(self) -> None:
        assert not _watsonx_available()

    def test_true_when_all_vars_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WATSONX_API_KEY", "fake-key")
        monkeypatch.setenv("WATSONX_URL", "https://fake.url")
        monkeypatch.setenv("WATSONX_PROJECT_ID", "fake-project")
        assert _watsonx_available()

    def test_false_when_only_partial_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WATSONX_API_KEY", "fake-key")
        # URL and PROJECT_ID not set
        assert not _watsonx_available()


# ---------------------------------------------------------------------------
# get_followup_question
# ---------------------------------------------------------------------------

class TestGetFollowupQuestion:
    def test_returns_question_for_missing_who(self) -> None:
        q = get_followup_question(["who"], 0)
        assert q is not None
        assert len(q) > 0

    def test_returns_question_for_missing_duration(self) -> None:
        q = get_followup_question(["duration"], 0)
        assert q is not None

    def test_returns_none_when_limit_reached(self) -> None:
        q = get_followup_question(["who", "duration"], 2)
        assert q is None

    def test_returns_none_when_no_missing_fields(self) -> None:
        q = get_followup_question([], 0)
        assert q is None

    def test_returns_none_after_two_questions(self) -> None:
        q1 = get_followup_question(["who", "duration"], 0)
        q2 = get_followup_question(["duration"], 1)
        q3 = get_followup_question(["duration"], 2)
        assert q1 is not None
        assert q2 is not None
        assert q3 is None

    def test_prioritises_who_over_duration(self) -> None:
        q = get_followup_question(["who", "duration"], 0)
        # "who" question should be returned first
        assert "yourself" in q.lower() or "someone" in q.lower()
