"""Test that every fact shown in the hospital cards also appears in answer_text.

Design requirement (item 5): the card text and the final_check answer must come
from the same structured object (result.ranked).  This test verifies that every
hospital name, phone and address shown in a card is also present in the checked
answer_text, so that final_check cannot be bypassed by rendering facts in the UI
that are absent from the text it validates.

Only the hospitals_us path is exercised here (the demo path renders cards
differently and is not the focus of these UI changes).
"""

from __future__ import annotations

import os
import re

import pytest

# Force offline / hospitals_us mode.
for _k in ("WATSONX_API_KEY", "WATSONX_URL", "WATSONX_PROJECT_ID"):
    os.environ.pop(_k, None)
os.environ.setdefault("DATA_SOURCE", "hospitals_us")

from src.orchestrator import run_session  # noqa: E402
from src.advisor import RankedHospital  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise_phone(phone: str) -> str:
    """Strip all non-digit characters for comparison."""
    return re.sub(r"\D", "", phone)


def _card_facts_for_result(session_result) -> list[dict]:
    """Extract the facts that would be rendered in cards from result.ranked.

    Returns a list of dicts with keys: name, phone, address, city, state, zip.
    Each dict corresponds to one card.
    """
    facts = []
    for r in session_result.ranked:
        if not isinstance(r, RankedHospital):
            continue
        h = r.hospital
        facts.append({
            "id": h.id,
            "name": (h.display_name or h.name.title()).lower(),
            "phone_digits": _normalise_phone(h.phone) if h.phone else "",
            "address": h.address.lower() if h.address else "",
            "city": h.city.lower() if h.city else "",
            "state": h.state.lower() if h.state else "",
        })
    return facts


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCardFactsInAnswerText:
    """Every fact rendered in a hospital card must appear in answer_text."""

    # Use a well-known location that reliably returns results.
    _AREA = '{"state": "AL", "zip": "36420"}'

    def _get_result(self, concern: str = "I have a bad cough and need to see a doctor"):
        return run_session(user_text=concern, area=self._AREA)

    def test_hospital_names_appear_in_answer_text(self) -> None:
        """Every hospital name rendered in a card must appear in answer_text."""
        result = self._get_result()
        if not result.ranked or not isinstance(result.ranked[0], RankedHospital):
            pytest.skip("No ranked hospitals returned for this area")

        answer_lower = result.answer_text.lower()
        for card in _card_facts_for_result(result):
            assert card["name"] in answer_lower, (
                f"Hospital name '{card['name']}' (ID {card['id']}) appears in cards "
                f"but is missing from answer_text.\n\nanswer_text:\n{result.answer_text[:400]}"
            )

    def test_hospital_phones_appear_in_answer_text(self) -> None:
        """Every hospital phone rendered in a card must appear (as digits) in answer_text."""
        result = self._get_result()
        if not result.ranked or not isinstance(result.ranked[0], RankedHospital):
            pytest.skip("No ranked hospitals returned for this area")

        answer_digits = _normalise_phone(result.answer_text)
        for card in _card_facts_for_result(result):
            if not card["phone_digits"]:
                continue
            assert card["phone_digits"] in answer_digits, (
                f"Phone digits '{card['phone_digits']}' for hospital '{card['name']}' "
                f"(ID {card['id']}) appear in cards but are missing from answer_text."
            )

    def test_hospital_addresses_appear_in_answer_text(self) -> None:
        """Every hospital address rendered in a card must appear in answer_text."""
        result = self._get_result()
        if not result.ranked or not isinstance(result.ranked[0], RankedHospital):
            pytest.skip("No ranked hospitals returned for this area")

        answer_lower = result.answer_text.lower()
        for card in _card_facts_for_result(result):
            if not card["address"]:
                continue
            assert card["address"] in answer_lower, (
                f"Address '{card['address']}' for hospital '{card['name']}' "
                f"(ID {card['id']}) appears in cards but is missing from answer_text."
            )

    def test_answer_text_passes_final_check_for_all_ranked_ids(self) -> None:
        """final_check must pass on the same answer_text that produced the cards."""
        from src.checks import final_check
        result = self._get_result()
        if not result.ranked or not isinstance(result.ranked[0], RankedHospital):
            pytest.skip("No ranked hospitals returned for this area")

        cited_ids = [r.hospital.id for r in result.ranked if isinstance(r, RankedHospital)]
        check = final_check(
            result.answer_text,
            cited_ids,
            tier=result.tier,
            hospital_ids=cited_ids,
        )
        assert check.passed, (
            f"final_check failed on answer_text that produced the cards.\n"
            f"Problems: {check.problems}\n\nanswer_text:\n{result.answer_text[:600]}"
        )

    def test_display_intro_is_short_friendly_sentence(self) -> None:
        """display_intro must be shorter than answer_text and not contain 'Option 1:'."""
        result = self._get_result()
        if not hasattr(result, "display_intro"):
            pytest.skip("display_intro field not present")
        assert "Option 1:" not in result.display_intro, (
            "display_intro must not contain 'Option 1:' — it should be a short friendly sentence."
        )
        assert len(result.display_intro) < len(result.answer_text) or not result.ranked, (
            "display_intro should be shorter than answer_text for the hospitals_us path "
            "when ranked results are present."
        )

    def test_display_intro_mentions_location(self) -> None:
        """display_intro for hospitals_us routine path must mention a location."""
        result = self._get_result()
        if not result.ranked or result.tier not in ("routine", "urgent"):
            pytest.skip("No ranked hospitals or unexpected tier")
        # The intro should contain either a city name, state abbreviation, or 'nearest'.
        intro = result.display_intro.lower()
        assert any(word in intro for word in ("al", "andalusia", "nearest", "based on")), (
            f"display_intro does not mention location or 'nearest'.\n"
            f"display_intro: {result.display_intro!r}"
        )


class TestFormatDistanceLine:
    """Unit tests for the _format_distance_line helper in app.py."""

    def _call(self, hosp_city: str, hosp_state: str, hosp_zip: str,
              distance_miles: float | None,
              selected_city: str | None, selected_zip: str | None) -> str:
        from app import _format_distance_line

        class _H:
            city = hosp_city
            state = hosp_state
            zip = hosp_zip
            distance_miles = None

        h = _H()
        h.distance_miles = distance_miles
        return _format_distance_line(h, selected_city, selected_zip)

    def test_city_match_shows_in_label(self) -> None:
        line = self._call("ANDALUSIA", "AL", "36420", 0.0, "ANDALUSIA", None)
        assert line.startswith("📍 In ")
        assert "Andalusia" in line
        assert "0.0" not in line
        assert "0 miles" not in line

    def test_zip_match_shows_in_label(self) -> None:
        line = self._call("ANDALUSIA", "AL", "36420", 0.0, None, "36420")
        assert line.startswith("📍 In ")
        assert "0.0" not in line

    def test_city_mismatch_shows_approx_miles(self) -> None:
        line = self._call("BREWTON", "AL", "36426", 12.3, "ANDALUSIA", None)
        assert "12 miles" in line or "12.0" in line or "About 12" in line
        assert "0 miles" not in line

    def test_no_distance_none_returns_empty(self) -> None:
        line = self._call("BREWTON", "AL", "36426", None, None, None)
        assert line == ""

    def test_never_shows_0_point_0_miles(self) -> None:
        """0.0 miles must never appear in any output from this function."""
        # City match path.
        line_city = self._call("ANDALUSIA", "AL", "36420", 0.0, "ANDALUSIA", None)
        assert "0.0" not in line_city
        # ZIP match path.
        line_zip = self._call("ANDALUSIA", "AL", "36420", 0.0, None, "36420")
        assert "0.0" not in line_zip

    def test_case_insensitive_city_match(self) -> None:
        """City matching must be case-insensitive."""
        line = self._call("ANDALUSIA", "AL", "36420", 0.1, "andalusia", None)
        assert line.startswith("📍 In ")
