"""Tests for src/checks.py — includes deliberate failing examples for every check."""

from __future__ import annotations

import pytest

from src.checks import (
    CheckResult,
    _check_banned_phrases,
    _check_last_verified,
    _check_phones,
    _check_service_ids,
    _check_service_names,
    _check_urgent_advice,
    final_check,
)

# ---------------------------------------------------------------------------
# Fixtures: real service data from services.json (SVC001 + SVC002)
# ---------------------------------------------------------------------------

# SVC001: Greenfield Community Pharmacy, phone 020-7000-0101, last_verified 2025-05-20
# SVC002: Greenfield Family Practice,    phone 020-7000-0102, last_verified 2025-05-22

_GOOD_ANSWER = (
    "We recommend Greenfield Community Pharmacy (SVC001). "
    "Phone: 020-7000-0101. Data last verified: 2025-05-20. "
    "They offer prescription dispensing and are about 0.2 km away. "
    "Please call them today to arrange your prescription."
)

_GOOD_IDS = ["SVC001"]


# ---------------------------------------------------------------------------
# final_check — passing cases
# ---------------------------------------------------------------------------

class TestFinalCheckPassing:
    def test_good_answer_passes(self) -> None:
        result = final_check(_GOOD_ANSWER, _GOOD_IDS, tier="routine")
        assert result.passed is True
        assert result.problems == []

    def test_returns_check_result(self) -> None:
        result = final_check(_GOOD_ANSWER, _GOOD_IDS)
        assert isinstance(result, CheckResult)

    def test_empty_cited_ids_passes_when_text_clean(self) -> None:
        result = final_check("Please call your GP today for an appointment.", [], tier="routine")
        assert result.passed is True

    def test_urgent_tier_with_urgent_language_passes(self) -> None:
        text = (
            "Greenfield Urgent Treatment Centre (SVC003). "
            "Phone: 020-7000-0103. Data last verified: 2025-06-01. "
            "You should attend today — do not wait."
        )
        result = final_check(text, ["SVC003"], tier="urgent")
        assert result.passed is True

    def test_multiple_services_all_present_passes(self) -> None:
        text = (
            "Option 1: Greenfield Community Pharmacy (SVC001). "
            "Phone: 020-7000-0101. Last verified: 2025-05-20. "
            "Option 2: Greenfield Family Practice (SVC002). "
            "Phone: 020-7000-0102. Last verified: 2025-05-22. "
            "Please contact them today."
        )
        result = final_check(text, ["SVC001", "SVC002"], tier="routine")
        assert result.passed is True


# ---------------------------------------------------------------------------
# Deliberately failing examples (one per check type)
# ---------------------------------------------------------------------------

class TestFinalCheckFailing:
    def test_unknown_service_id_fails(self) -> None:
        """Deliberately bad: cites a non-existent service ID."""
        result = final_check(_GOOD_ANSWER, ["SVC999"], tier="routine")
        assert result.passed is False
        assert any("SVC999" in p for p in result.problems)

    def test_service_name_missing_from_text_fails(self) -> None:
        """Deliberately bad: cites SVC002 but omits its name from the text."""
        text = (
            "Please call 020-7000-0102. Data last verified: 2025-05-22. "
            "Contact them today for an appointment."
        )
        result = final_check(text, ["SVC002"], tier="routine")
        assert result.passed is False
        assert any("Greenfield Family Practice" in p for p in result.problems)

    def test_phone_missing_from_text_fails(self) -> None:
        """Deliberately bad: name and ID present but phone number omitted."""
        text = (
            "We recommend Greenfield Community Pharmacy (SVC001). "
            "Data last verified: 2025-05-20. They are nearby."
        )
        result = final_check(text, ["SVC001"], tier="routine")
        assert result.passed is False
        assert any("020-7000-0101" in p for p in result.problems)

    def test_banned_diagnosis_phrase_fails(self) -> None:
        """Deliberately bad: answer contains a banned diagnosis phrase."""
        text = (
            "Greenfield Community Pharmacy (SVC001). "
            "Phone: 020-7000-0101. Last verified: 2025-05-20. "
            "You have an infection and should see a pharmacist."
        )
        result = final_check(text, ["SVC001"], tier="routine")
        assert result.passed is False
        assert any("banned phrase" in p.lower() for p in result.problems)

    def test_banned_treatment_phrase_fails(self) -> None:
        """Deliberately bad: answer tells the user to take ibuprofen."""
        text = (
            "Greenfield Community Pharmacy (SVC001). "
            "Phone: 020-7000-0101. Last verified: 2025-05-20. "
            "Take ibuprofen for the pain and visit the pharmacist."
        )
        result = final_check(text, ["SVC001"], tier="routine")
        assert result.passed is False
        assert any("ibuprofen" in p.lower() for p in result.problems)

    def test_last_verified_missing_fails(self) -> None:
        """Deliberately bad: last-verified date absent from the answer."""
        text = (
            "Greenfield Community Pharmacy (SVC001). "
            "Phone: 020-7000-0101. They offer prescriptions."
        )
        result = final_check(text, ["SVC001"], tier="routine")
        assert result.passed is False
        assert any("2025-05-20" in p for p in result.problems)

    def test_urgent_tier_without_urgent_language_fails(self) -> None:
        """Deliberately bad: urgent tier but no urgent-action language."""
        text = (
            "Greenfield Urgent Treatment Centre (SVC003). "
            "Phone: 020-7000-0103. Last verified: 2025-06-01. "
            "This centre offers minor injuries care."
        )
        result = final_check(text, ["SVC003"], tier="urgent")
        assert result.passed is False
        assert any("urgent" in p.lower() for p in result.problems)

    def test_multiple_failures_all_reported(self) -> None:
        """Deliberately bad answer that fails multiple checks simultaneously."""
        text = "You have an infection. Take antibiotics."
        result = final_check(text, ["SVC999"], tier="urgent")
        assert result.passed is False
        # Should report: bad ID, banned diagnosis phrase, banned treatment phrase,
        # missing urgent advice (at minimum 3 problems)
        assert len(result.problems) >= 3


# ---------------------------------------------------------------------------
# Individual check function unit tests
# ---------------------------------------------------------------------------

class TestCheckServiceIds:
    def test_valid_id_returns_no_problems(self) -> None:
        from src.checks import _load_services_index
        idx = _load_services_index()
        problems = _check_service_ids("", ["SVC001"], idx)
        assert problems == []

    def test_invalid_id_returns_problem(self) -> None:
        problems = _check_service_ids("", ["SVC999"], {})
        assert len(problems) == 1
        assert "SVC999" in problems[0]

    def test_empty_ids_returns_no_problems(self) -> None:
        problems = _check_service_ids("some text", [], {})
        assert problems == []


class TestCheckServiceNames:
    def test_name_present_returns_no_problems(self) -> None:
        idx = {"SVC001": {"name": "Test Clinic", "phone": "000", "last_verified": "2025-01-01"}}
        problems = _check_service_names("Visit Test Clinic for care.", ["SVC001"], idx)
        assert problems == []

    def test_name_absent_returns_problem(self) -> None:
        idx = {"SVC001": {"name": "Test Clinic", "phone": "000", "last_verified": "2025-01-01"}}
        problems = _check_service_names("Visit the pharmacy nearby.", ["SVC001"], idx)
        assert len(problems) == 1
        assert "Test Clinic" in problems[0]

    def test_case_insensitive_match(self) -> None:
        idx = {"SVC001": {"name": "Test Clinic", "phone": "000", "last_verified": "2025-01-01"}}
        problems = _check_service_names("Visit TEST CLINIC today.", ["SVC001"], idx)
        assert problems == []


class TestCheckPhones:
    def test_phone_present_returns_no_problems(self) -> None:
        idx = {"SVC001": {"name": "Test", "phone": "020-7000-0101", "last_verified": "2025-01-01"}}
        problems = _check_phones("Call 020-7000-0101 now.", ["SVC001"], idx)
        assert problems == []

    def test_phone_present_without_hyphens(self) -> None:
        idx = {"SVC001": {"name": "Test", "phone": "020-7000-0101", "last_verified": "2025-01-01"}}
        problems = _check_phones("Call 02070000101 now.", ["SVC001"], idx)
        assert problems == []

    def test_phone_absent_returns_problem(self) -> None:
        idx = {"SVC001": {"name": "Test", "phone": "020-7000-0101", "last_verified": "2025-01-01"}}
        problems = _check_phones("Visit the clinic.", ["SVC001"], idx)
        assert len(problems) == 1
        assert "020-7000-0101" in problems[0]


class TestCheckBannedPhrases:
    def test_clean_text_returns_no_problems(self) -> None:
        problems = _check_banned_phrases("Please contact your GP for an appointment today.")
        assert problems == []

    def test_diagnosis_phrase_detected(self) -> None:
        problems = _check_banned_phrases("You have an infection that needs treatment.")
        assert len(problems) >= 1

    def test_treatment_phrase_detected(self) -> None:
        problems = _check_banned_phrases("Take ibuprofen for pain relief.")
        assert len(problems) >= 1

    def test_case_insensitive(self) -> None:
        problems = _check_banned_phrases("TAKE IBUPROFEN immediately.")
        assert len(problems) >= 1

    def test_condition_name_detected(self) -> None:
        problems = _check_banned_phrases("This looks like appendicitis.")
        assert len(problems) >= 1


class TestCheckLastVerified:
    def test_date_present_returns_no_problems(self) -> None:
        idx = {"SVC001": {"name": "T", "phone": "0", "last_verified": "2025-05-20"}}
        problems = _check_last_verified("Data last verified: 2025-05-20.", ["SVC001"], idx)
        assert problems == []

    def test_date_absent_returns_problem(self) -> None:
        idx = {"SVC001": {"name": "T", "phone": "0", "last_verified": "2025-05-20"}}
        problems = _check_last_verified("No date here.", ["SVC001"], idx)
        assert len(problems) == 1
        assert "2025-05-20" in problems[0]

    def test_no_last_verified_field_skipped(self) -> None:
        idx = {"SVC001": {"name": "T", "phone": "0"}}
        problems = _check_last_verified("No date here.", ["SVC001"], idx)
        assert problems == []


class TestCheckUrgentAdvice:
    def test_routine_tier_never_fails(self) -> None:
        problems = _check_urgent_advice("No advice here.", "routine")
        assert problems == []

    def test_emergency_tier_never_fails(self) -> None:
        problems = _check_urgent_advice("No advice here.", "emergency")
        assert problems == []

    def test_urgent_with_today_passes(self) -> None:
        problems = _check_urgent_advice("Please call today.", "urgent")
        assert problems == []

    def test_urgent_with_call_passes(self) -> None:
        problems = _check_urgent_advice("Call 999 now.", "urgent")
        assert problems == []

    def test_urgent_without_markers_fails(self) -> None:
        problems = _check_urgent_advice("Visit the clinic when convenient.", "urgent")
        assert len(problems) == 1

    def test_urgent_with_do_not_wait_passes(self) -> None:
        problems = _check_urgent_advice("Do not wait — attend the centre.", "urgent")
        assert problems == []
