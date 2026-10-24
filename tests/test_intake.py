"""Tests for src/intake.py — offline fallback only (no watsonx.ai credentials needed)."""

from __future__ import annotations

import os
import pytest

# Force offline mode for the entire test session by clearing watsonx env vars.
for _k in ("WATSONX_API_KEY", "WATSONX_URL", "WATSONX_PROJECT_ID"):
    os.environ.pop(_k, None)

from src.intake import (  # noqa: E402  (import after env manipulation)
    CaseResult,
    _extract_denied_symptoms_offline,
    _extract_duration_offline,
    _extract_who_offline,
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


# ---------------------------------------------------------------------------
# denied_symptoms extraction
# ---------------------------------------------------------------------------

class TestDeniedSymptoms:
    def test_no_fever_captured(self) -> None:
        result = _extract_denied_symptoms_offline("She has had a sore throat and no fever.")
        assert any("fever" in d for d in result)

    def test_no_symptoms_empty_list(self) -> None:
        result = _extract_denied_symptoms_offline("I have a headache.")
        assert result == []

    def test_negated_phrase_not_in_concern(self) -> None:
        result = _call("My 6-year-old daughter has had a sore throat for three days and no fever.")
        assert "fever" not in result.concern.lower(), (
            f"denied symptom 'fever' appeared in concern: {result.concern!r}"
        )
        assert any("fever" in d for d in result.denied_symptoms)

    def test_multiple_denied_symptoms(self) -> None:
        result = _call("She has no fever and no cough but has stomach pain.")
        assert any("fever" in d for d in result.denied_symptoms)
        assert any("cough" in d for d in result.denied_symptoms)

    def test_no_generic_negation_without_symptom(self) -> None:
        # "no problem" contains no symptom word → not captured
        result = _extract_denied_symptoms_offline("I have no problem walking.")
        assert result == []

    def test_denied_symptoms_is_list(self) -> None:
        result = _call("I have a headache.")
        assert isinstance(result.denied_symptoms, list)

    def test_validate_accepts_denied_symptoms(self) -> None:
        result = _call("I have a sore knee, no swelling, for about three days.")
        assert isinstance(result.denied_symptoms, list)
        # denied_symptoms must survive _validate (called inside _offline_extract via extract_case)
        from src.intake import _validate
        _validate(result)  # must not raise


# ---------------------------------------------------------------------------
# Six-year-old example (full-case integration)
# ---------------------------------------------------------------------------

class TestSixYearOldExample:
    """Regression tests for the canonical 6-year-old scenario."""

    _TEXT = "My 6-year-old daughter has had a sore throat for three days and no fever."
    _AREA = "Riverside"

    def setup_method(self) -> None:
        self.result = _call(self._TEXT, self._AREA)

    def test_who_includes_age(self) -> None:
        assert "6" in self.result.who

    def test_who_includes_relation(self) -> None:
        assert "daughter" in self.result.who.lower()

    def test_who_full_form(self) -> None:
        assert self.result.who == "my 6-year-old daughter"

    def test_duration_three_days(self) -> None:
        assert "three" in self.result.duration.lower() or "3" in self.result.duration

    def test_duration_not_empty(self) -> None:
        assert self.result.duration != ""

    def test_duration_not_missing(self) -> None:
        assert "duration" not in self.result.missing_fields

    def test_concern_contains_sore_throat(self) -> None:
        assert "sore throat" in self.result.concern.lower()

    def test_concern_does_not_contain_fever(self) -> None:
        assert "fever" not in self.result.concern.lower()

    def test_denied_symptoms_has_no_fever(self) -> None:
        assert any("fever" in d for d in self.result.denied_symptoms)

    def test_no_missing_fields(self) -> None:
        assert self.result.missing_fields == []


# ---------------------------------------------------------------------------
# Word-number duration forms
# ---------------------------------------------------------------------------

class TestWordNumberDuration:
    def test_three_days(self) -> None:
        assert _extract_duration_offline("had it for three days") != ""

    def test_two_weeks(self) -> None:
        result = _extract_duration_offline("has had this for two weeks")
        assert "two" in result.lower() or "week" in result.lower()

    def test_for_2_days(self) -> None:
        result = _extract_duration_offline("I have been breathless for 2 days")
        assert "2" in result or "day" in result.lower()

    def test_since_yesterday(self) -> None:
        result = _extract_duration_offline("coughing since yesterday")
        assert "yesterday" in result.lower()

    def test_couple_of_days(self) -> None:
        result = _extract_duration_offline("I've felt this way for a couple of days")
        assert result != ""

    def test_five_hours(self) -> None:
        result = _extract_duration_offline("This started five hours ago")
        assert "five" in result.lower() or "hour" in result.lower()


# ---------------------------------------------------------------------------
# Verbatim concern (user's own words, not bag-of-words)
# ---------------------------------------------------------------------------

class TestVerbatimConcern:
    def test_concern_preserves_adjective_noun_pair(self) -> None:
        result = _call("I have a sore throat.")
        assert "sore throat" in result.concern.lower()

    def test_concern_preserves_phrase_not_individual_words(self) -> None:
        # Verify concern is a readable phrase, not jumbled keywords.
        result = _call("My mum has a very bad headache since this morning.")
        assert "headache" in result.concern.lower()

    def test_concern_does_not_contain_who_phrase(self) -> None:
        # "my 6-year-old daughter" should be stripped from concern.
        result = _call("My 6-year-old daughter has a cough for two days.")
        assert "6-year-old" not in result.concern
        assert "daughter" not in result.concern.lower()

    def test_concern_does_not_contain_duration(self) -> None:
        result = _call("I have had a rash for three days.")
        assert "three days" not in result.concern.lower()

    def test_who_age_with_relation_full_form(self) -> None:
        assert _extract_who_offline("My 6-year-old daughter has a cough.") == "my 6-year-old daughter"

    def test_who_age_without_relation(self) -> None:
        assert _extract_who_offline("My 4-year-old has been crying.") == "my 4-year-old"

    def test_who_bare_age(self) -> None:
        result = _extract_who_offline("Calling about a 3-year-old with a rash.")
        assert "3" in result


# ---------------------------------------------------------------------------
# Handoff summary includes denied_symptoms
# ---------------------------------------------------------------------------

class TestHandoffSummaryDenied:
    def test_denied_symptoms_appear_in_handoff(self) -> None:
        import os
        os.environ.pop("WATSONX_API_KEY", None)
        os.environ.pop("WATSONX_URL", None)
        os.environ.pop("WATSONX_PROJECT_ID", None)
        from src.orchestrator import create_handoff_summary, run_session
        result = run_session(
            "My 6-year-old daughter has had a sore throat for three days and no fever.",
            "Riverside",
        )
        summary = create_handoff_summary(result)
        assert "Denied symptoms" in summary
        assert "fever" in summary.lower()

    def test_handoff_omits_denied_when_empty(self) -> None:
        import os
        os.environ.pop("WATSONX_API_KEY", None)
        os.environ.pop("WATSONX_URL", None)
        os.environ.pop("WATSONX_PROJECT_ID", None)
        from src.orchestrator import create_handoff_summary, run_session
        result = run_session(
            "I need a repeat prescription for my blood pressure tablets.",
            "Westbury",
        )
        summary = create_handoff_summary(result)
        assert "Denied symptoms" not in summary
