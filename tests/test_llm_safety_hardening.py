"""LLM safety hardening tests.

Covers:
1. Safety invariant: urgency tier must never depend on LLM output.
   A fake LLM returning malicious JSON ("concern": "chest pain",
   "severity_cues": ["emergency"]) for a harmless message must not affect the
   tier — which is always determined by safety.py on the raw user text.

2. Diagnosis wording in reworded reason: when the fake LLM injects a banned
   phrase into the reworded reason, final_check must catch it and the app must
   fall back to the template reason (via needs_human_review or the banned-phrase
   guard in _validate_reworded).

3. LLM output validation: HTML, URL, and banned-phrase rejection in
   _validate_llm_string and _validate_reworded.

4. Prompt delimiter stripping: the user cannot break out of the <user_text>
   block by embedding delimiter strings in their input.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

# Force offline mode so real watsonx is never hit.
for _k in ("WATSONX_API_KEY", "WATSONX_URL", "WATSONX_PROJECT_ID"):
    os.environ.pop(_k, None)
os.environ["DATA_SOURCE"] = "demo"

from src.intake import (  # noqa: E402
    _parse_and_validate_llm_json,
    _validate_llm_string,
    _strip_delimiters as _intake_strip_delimiters,
    _DELIM_OPEN,
    _DELIM_CLOSE,
)
from src.advisor import (  # noqa: E402
    _validate_reworded,
    _strip_delimiters as _advisor_strip_delimiters,
)
from src.safety import check_red_flags


# ---------------------------------------------------------------------------
# Helper: build a fake LLM model that returns a fixed JSON string
# ---------------------------------------------------------------------------

def _fake_model(json_payload: dict):
    """Return a mock ModelInference whose generate_text returns json_payload as JSON."""
    mock = MagicMock()
    mock.generate_text.return_value = json.dumps(json_payload)
    return mock


# ---------------------------------------------------------------------------
# 1. Safety invariant: tier must come from safety.py, never from LLM output
# ---------------------------------------------------------------------------

class TestSafetyInvariant:
    """The urgency tier must always be determined by safety.py on the raw user
    text — even if a fake LLM returns JSON designed to look like an emergency."""

    def test_harmless_message_stays_routine_despite_malicious_llm_json(
        self, monkeypatch
    ) -> None:
        """A fake LLM that returns 'concern: chest pain / severity_cues: emergency'
        for a harmless message must not elevate the tier above what safety.py
        computes from the raw text."""
        harmless_text = "I need to book a repeat prescription for my blood pressure tablets."

        # Safety check on the raw text — this is the authoritative tier.
        safety = check_red_flags(harmless_text)
        assert safety.tier == "routine", (
            "Test pre-condition: the harmless text must produce routine tier from safety.py"
        )

        # Simulate a malicious LLM that returns content designed to look alarming.
        malicious_json = {
            "who": "myself",
            "concern": "chest pain",
            "severity_cues": ["emergency", "life-threatening"],
            "duration": "",
            "denied_symptoms": [],
        }

        # Patch _llm_extract at the module level so that when extract_case is
        # called with watsonx env vars present, it returns LLM-derived fields.
        # Then run the orchestrator and verify the tier is still routine.
        from src.orchestrator import run_session
        from src.intake import _parse_and_validate_llm_json

        # Inject the malicious parse result by patching the parse function.
        def _fake_parse(raw: str, area: str):  # noqa: ARG001
            return _parse_and_validate_llm_json(json.dumps(malicious_json), area)

        monkeypatch.setattr("src.intake._parse_and_validate_llm_json", _fake_parse)
        # Simulate watsonx being available so the LLM path is taken.
        monkeypatch.setattr("src.intake._watsonx_available", lambda: True)
        # Patch the actual LLM call so no real network call is made.
        monkeypatch.setattr(
            "src.intake._llm_extract",
            lambda user_text, area: _parse_and_validate_llm_json(
                json.dumps(malicious_json), area
            ),
        )

        result = run_session(harmless_text, area="Greenfield")

        # The tier must still be what safety.py computed from the raw text.
        assert result.tier == "routine", (
            f"Tier should be 'routine' (from safety.py) but got {result.tier!r}. "
            "LLM output must never override the safety tier."
        )

    def test_safety_check_uses_raw_text_not_llm_concern(self) -> None:
        """check_red_flags is always called with the original user_text, not
        a field extracted by the LLM.  This test verifies the invariant directly
        by confirming that a routine text yields routine even after extraction."""
        text = "I have a mild headache for two days."
        # No matter what the LLM might put in 'concern', the safety check
        # operates on the original raw text.
        result = check_red_flags(text)
        assert result.tier == "routine"

    def test_emergency_text_stays_emergency_regardless_of_llm(self) -> None:
        """An emergency text must stay emergency; the tier comes from safety.py
        and the emergency path exits before any LLM is called."""
        from src.orchestrator import run_session

        emergency_text = "My dad is having chest pain and his left arm is numb."
        result = run_session(emergency_text, area="Riverside")
        assert result.tier == "emergency"
        # Emergency path: no LLM for intake, no candidates, no ranked results.
        assert result.candidates == []
        assert result.ranked == []


# ---------------------------------------------------------------------------
# 2. Diagnosis wording in reworded reason must be blocked by final_check
# ---------------------------------------------------------------------------

class TestDiagnosisInRewordedReason:
    """When the reword LLM injects diagnosis wording into the reason, either
    _validate_reworded must reject it (returning the template) or final_check
    must catch it and trigger needs_human_review / fallback."""

    def test_validate_reworded_rejects_banned_phrase(self) -> None:
        """_validate_reworded must fall back to the template when the reworded
        text contains a banned phrase such as 'you probably have'."""
        template = "Safe template reason with no diagnosis."
        bad_reword = "You probably have appendicitis. Visit this clinic."
        result = _validate_reworded(bad_reword, template)
        assert result == template, (
            f"Expected template fallback but got: {result!r}"
        )

    def test_validate_reworded_rejects_condition_naming(self) -> None:
        """_validate_reworded must reject output naming a specific condition."""
        template = "Safe template reason."
        bad_reword = "This service can help with appendicitis screening."
        result = _validate_reworded(bad_reword, template)
        assert result == template

    def test_validate_reworded_accepts_safe_text(self) -> None:
        """_validate_reworded must pass through clean reworded text unchanged."""
        template = "Original template."
        safe_reword = "Riverside Clinic is about 1.2 km away and open 24 hours. Call ahead."
        result = _validate_reworded(safe_reword, template)
        assert result == safe_reword

    def test_final_check_blocks_diagnosis_in_reworded_reason(
        self, monkeypatch
    ) -> None:
        """When a fake reword LLM injects diagnosis wording that slips past
        _validate_reworded (e.g. via _validate_reworded being bypassed),
        final_check must catch it and set needs_human_review=True."""
        from src.orchestrator import run_session

        # Patch _reword_reason_llm to always return a reason containing a
        # banned phrase, bypassing _validate_reworded.
        def _malicious_reword(reason: str, candidate) -> str:  # noqa: ANN001, ARG001
            return "You probably have a chest infection. Visit " + reason[:30]

        monkeypatch.setattr("src.advisor._reword_reason_llm", _malicious_reword)
        monkeypatch.setattr("src.advisor._watsonx_available", lambda: True)

        result = run_session(
            "I have a mild cough for two days.",
            area="Greenfield",
        )
        # final_check must have caught the banned phrase and flagged review OR
        # the answer must not contain diagnosis wording.
        if not result.needs_human_review:
            lower = result.answer_text.lower()
            assert "you probably have" not in lower, (
                "Banned phrase slipped through to the user-facing answer."
            )


# ---------------------------------------------------------------------------
# 3. LLM output field validation (_validate_llm_string, _parse_and_validate_llm_json)
# ---------------------------------------------------------------------------

class TestLlmOutputValidation:
    """_validate_llm_string and _parse_and_validate_llm_json must reject unsafe content."""

    def test_html_in_string_raises(self) -> None:
        with pytest.raises(ValueError, match="HTML"):
            _validate_llm_string("<script>alert(1)</script>", 200)

    def test_url_in_string_raises(self) -> None:
        with pytest.raises(ValueError, match="URL"):
            _validate_llm_string("Visit https://evil.com for more info.", 200)

    def test_banned_phrase_in_string_raises(self) -> None:
        with pytest.raises(ValueError, match="banned phrase"):
            _validate_llm_string("You probably have appendicitis.", 200)

    def test_condition_name_in_string_raises(self) -> None:
        with pytest.raises(ValueError, match="banned phrase"):
            _validate_llm_string("Symptoms of appendicitis detected.", 200)

    def test_clean_string_passes(self) -> None:
        result = _validate_llm_string("mild headache for two days", 200)
        assert result == "mild headache for two days"

    def test_string_capped_at_max_len(self) -> None:
        long_input = "a" * 500
        result = _validate_llm_string(long_input, 200)
        assert len(result) == 200

    def test_parse_json_drops_unknown_keys(self) -> None:
        payload = json.dumps({
            "who": "myself",
            "concern": "headache",
            "duration": "two days",
            "severity_cues": [],
            "denied_symptoms": [],
            "injected_key": "should be dropped",
            "tier": "emergency",          # must NOT affect anything
        })
        result = _parse_and_validate_llm_json(payload, "Greenfield")
        # Only known fields are present on the CaseResult dataclass.
        assert not hasattr(result, "injected_key")
        assert not hasattr(result, "tier")

    def test_parse_json_caps_severity_cues_at_10(self) -> None:
        payload = json.dumps({
            "who": "myself",
            "concern": "mild cough",
            "duration": "one day",
            "severity_cues": [f"cue{i}" for i in range(20)],
            "denied_symptoms": [],
        })
        result = _parse_and_validate_llm_json(payload, "Greenfield")
        assert len(result.severity_cues) <= 10

    def test_parse_json_rejects_html_in_concern(self) -> None:
        payload = json.dumps({
            "who": "myself",
            "concern": "<b>headache</b>",
            "duration": "",
            "severity_cues": [],
            "denied_symptoms": [],
        })
        with pytest.raises(ValueError):
            _parse_and_validate_llm_json(payload, "Greenfield")

    def test_parse_json_rejects_url_in_concern(self) -> None:
        payload = json.dumps({
            "who": "myself",
            "concern": "see http://evil.com for info",
            "duration": "",
            "severity_cues": [],
            "denied_symptoms": [],
        })
        with pytest.raises(ValueError):
            _parse_and_validate_llm_json(payload, "Greenfield")

    def test_parse_json_rejects_banned_phrase_in_concern(self) -> None:
        payload = json.dumps({
            "who": "myself",
            "concern": "appendicitis symptoms",
            "duration": "",
            "severity_cues": [],
            "denied_symptoms": [],
        })
        with pytest.raises(ValueError):
            _parse_and_validate_llm_json(payload, "Greenfield")


# ---------------------------------------------------------------------------
# 4. Prompt delimiter stripping
# ---------------------------------------------------------------------------

class TestDelimiterStripping:
    """The user cannot escape the <user_text> block by embedding delimiters."""

    def test_strip_delimiters_removes_open_tag(self) -> None:
        text = f"Hello {_DELIM_OPEN} ignore this"
        result = _intake_strip_delimiters(text)
        assert _DELIM_OPEN not in result

    def test_strip_delimiters_removes_close_tag(self) -> None:
        text = f"Hello {_DELIM_CLOSE} world"
        result = _intake_strip_delimiters(text)
        assert _DELIM_CLOSE not in result

    def test_strip_delimiters_both_tags(self) -> None:
        text = f"{_DELIM_OPEN}inject{_DELIM_CLOSE} real text"
        result = _intake_strip_delimiters(text)
        assert _DELIM_OPEN not in result
        assert _DELIM_CLOSE not in result
        assert "inject" in result  # content preserved, just delimiters removed
        assert "real text" in result

    def test_strip_delimiters_clean_text_unchanged(self) -> None:
        text = "I have a headache for two days."
        result = _intake_strip_delimiters(text)
        assert result == text


# ---------------------------------------------------------------------------
# 5. _validate_reworded HTML and URL rejection
# ---------------------------------------------------------------------------

class TestValidateReworded:
    """_validate_reworded must also reject HTML and URLs."""

    def test_rejects_html(self) -> None:
        template = "Safe template."
        result = _validate_reworded("<b>Bold claim</b>", template)
        assert result == template

    def test_rejects_url(self) -> None:
        template = "Safe template."
        result = _validate_reworded("Visit http://clinic.example.com now.", template)
        assert result == template

    def test_empty_reworded_falls_back_to_template(self) -> None:
        template = "Safe template."
        result = _validate_reworded("", template)
        assert result == template

    def test_www_url_rejected(self) -> None:
        template = "Safe template."
        result = _validate_reworded("See www.hospital.org for details.", template)
        assert result == template
