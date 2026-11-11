"""Tests for the 4 emergency-display fixes.

1. Placeholder check — no "[DEMO" or "{emergency_" in any loaded message.
2. Double-display fix — display_intro is a short sentence, NOT the full body.
3. Text-to-911 removal — "text" + number no longer appears; "cannot speak" does.
4. Action button — label "Call <number> now" with tel: URL.

All tests run offline (no LLM, no network).
"""

from __future__ import annotations

import os
import re

import pytest

# Ensure offline mode.
for _k in ("WATSONX_API_KEY", "WATSONX_URL", "WATSONX_PROJECT_ID"):
    os.environ.pop(_k, None)

os.environ["DATA_SOURCE"] = "demo"

from src.orchestrator import (  # noqa: E402
    SessionResult,
    _DEMO_EMERGENCY_NUMBER,
    _emergency_number,
    _load_messages,
    run_session,
    verify_no_placeholders,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_emergency() -> SessionResult:
    """Return a SessionResult with tier==emergency (anaphylaxis trigger)."""
    return run_session("I ate a peanut and my throat is swelling up and I can't breathe.", "Riverside")


# ---------------------------------------------------------------------------
# 1. Placeholder check
# ---------------------------------------------------------------------------

class TestPlaceholderCheck:
    """Verify that _load_messages() replaces all placeholders and the startup
    check catches any regression."""

    def test_no_brace_placeholder_in_loaded_messages(self) -> None:
        """No {emergency_number} or {emergency_region} must survive load."""
        messages = _load_messages()

        def _find(obj: object) -> list[str]:
            found: list[str] = []
            if isinstance(obj, str):
                if re.search(r"\{emergency_(?:number|region)\}", obj):
                    found.append(obj)
            elif isinstance(obj, dict):
                for v in obj.values():
                    found.extend(_find(v))
            elif isinstance(obj, list):
                for v in obj:
                    found.extend(_find(v))
            return found

        leftovers = _find(messages)
        assert leftovers == [], (
            "Unresolved brace placeholders found after _load_messages(): "
            + str(leftovers)
        )

    def test_no_demo_bracket_placeholder_in_loaded_messages(self) -> None:
        """No [DEMO... bracket placeholder must survive load."""
        messages = _load_messages()

        def _find(obj: object) -> list[str]:
            found: list[str] = []
            if isinstance(obj, str):
                if re.search(r"\[DEMO", obj):
                    found.append(obj)
            elif isinstance(obj, dict):
                for v in obj.values():
                    found.extend(_find(v))
            elif isinstance(obj, list):
                for v in obj:
                    found.extend(_find(v))
            return found

        leftovers = _find(messages)
        assert leftovers == [], (
            "Old [DEMO...] placeholders found after _load_messages(): "
            + str(leftovers)
        )

    def test_verify_no_placeholders_passes_on_clean_dict(self) -> None:
        """verify_no_placeholders() must not raise on already-filled messages."""
        messages = _load_messages()
        # Should not raise.
        verify_no_placeholders(messages)

    def test_verify_no_placeholders_raises_on_brace_placeholder(self) -> None:
        """verify_no_placeholders() must raise RuntimeError for a brace placeholder."""
        dirty = {"emergency": {"body": "Call {emergency_number} now."}}
        with pytest.raises(RuntimeError, match="Unresolved placeholder"):
            verify_no_placeholders(dirty)

    def test_verify_no_placeholders_raises_on_demo_bracket(self) -> None:
        """verify_no_placeholders() must raise RuntimeError for a [DEMO...] value."""
        dirty = {"emergency": {"body": "Call [DEMO-999] now."}}
        with pytest.raises(RuntimeError, match="Unresolved placeholder"):
            verify_no_placeholders(dirty)

    def test_emergency_answer_uses_configured_number_not_demo_bracket(self, monkeypatch) -> None:
        """When EMERGENCY_NUMBER=911, the answer must contain '911', not '[DEMO'."""
        monkeypatch.setenv("EMERGENCY_NUMBER", "911")
        result = _run_emergency()
        assert "911" in result.answer_text
        assert "[DEMO" not in result.answer_text

    def test_default_emergency_number_is_911(self) -> None:
        """With no env var set, _emergency_number() must return '911' (not [DEMO-999])."""
        # _DEMO_EMERGENCY_NUMBER is now '911' — confirm the constant.
        assert _DEMO_EMERGENCY_NUMBER == "911"

    def test_emergency_answer_never_contains_raw_brace(self) -> None:
        """The answer_text produced by run_session must never contain a brace placeholder."""
        result = _run_emergency()
        assert "{emergency_number}" not in result.answer_text
        assert "{emergency_region}" not in result.answer_text


# ---------------------------------------------------------------------------
# 2. Double-display: display_intro must be a short sentence, not the full body
# ---------------------------------------------------------------------------

class TestNoDoubleDisplay:
    """display_intro is stored in the chat bubble history; answer_text goes to
    the alert box.  They must be different for the emergency tier so the full
    message is not shown twice."""

    def test_display_intro_differs_from_answer_text(self) -> None:
        result = _run_emergency()
        assert result.tier == "emergency"
        assert result.display_intro != result.answer_text

    def test_display_intro_is_short(self) -> None:
        """display_intro must not contain the full emergency body paragraph."""
        result = _run_emergency()
        assert result.tier == "emergency"
        # The body has >50 chars; display_intro should be a short single sentence.
        assert len(result.display_intro) < len(result.answer_text)

    def test_display_intro_does_not_contain_do_not_drive(self) -> None:
        """The detailed instruction 'Do not drive yourself' belongs in the alert,
        not in the chat bubble."""
        result = _run_emergency()
        assert result.tier == "emergency"
        assert "do not drive" not in result.display_intro.lower()

    def test_answer_text_contains_full_body(self) -> None:
        """answer_text must contain the full emergency heading and body."""
        result = _run_emergency()
        assert result.tier == "emergency"
        assert "emergency" in result.answer_text.lower()
        assert "call" in result.answer_text.lower()


# ---------------------------------------------------------------------------
# 3. Text-to-911 removed; "cannot speak" added; needs_clinical_review flag
# ---------------------------------------------------------------------------

class TestMessageContent:
    """Validate the updated messages.json content."""

    def test_no_text_number_instruction_in_answer(self) -> None:
        """The phrase 'text 911' (or 'text <number>') must not appear."""
        result = _run_emergency()
        lower = result.answer_text.lower()
        # Look for 'text' immediately before a digit string (e.g. 'text 911')
        assert not re.search(r"\btext\s+\d", lower), (
            "Found a 'text <number>' instruction in the answer. "
            "Text-to-911 is not universally available and must be removed."
        )

    def test_cannot_speak_instruction_present(self) -> None:
        """'cannot speak' (or 'can't speak') must appear in the answer."""
        result = _run_emergency()
        lower = result.answer_text.lower()
        assert "cannot speak" in lower or "can't speak" in lower, (
            "The 'cannot speak / ask someone nearby' instruction is missing."
        )

    def test_ask_someone_nearby_present(self) -> None:
        """'ask someone nearby' must appear in the answer."""
        result = _run_emergency()
        lower = result.answer_text.lower()
        assert "ask someone nearby" in lower

    def test_messages_json_has_needs_clinical_review_flag(self) -> None:
        """The emergency message in messages.json must have needs_clinical_review: true."""
        import json
        from pathlib import Path
        data = json.loads(
            (Path(__file__).parent.parent / "data" / "messages.json").read_text(encoding="utf-8")
        )
        em = data["messages"]["emergency"]
        assert em.get("needs_clinical_review") is True, (
            "messages.json emergency entry is missing 'needs_clinical_review: true'."
        )

    def test_additional_note_field_removed(self) -> None:
        """The old 'additional_note' (text-to-911 sentence) must be gone from messages.json."""
        import json
        from pathlib import Path
        data = json.loads(
            (Path(__file__).parent.parent / "data" / "messages.json").read_text(encoding="utf-8")
        )
        em = data["messages"]["emergency"]
        assert "additional_note" not in em, (
            "The deprecated 'additional_note' field still exists in messages.json."
        )


# ---------------------------------------------------------------------------
# 4. Action button — "Call <number> now" with tel: link
# ---------------------------------------------------------------------------

class TestEmergencyButtonSpec:
    """These tests exercise get_button_spec() directly (pure function in app.py)."""

    def _make_result(self) -> SessionResult:
        return SessionResult(
            session_id="test",
            tier="emergency",
            state="emergency",
            case=None,
            safety=None,
            candidates=[],
            ranked=[],
            answer_text="",
            display_intro="",
            check_passed=True,
            needs_human_review=False,
            handoff_summary=None,
            followup_questions_asked=0,
            missing_fields=[],
            location=None,
        )

    def test_emergency_button_label_is_call_911_now_by_default(self, monkeypatch) -> None:
        """With EMERGENCY_NUMBER unset, button label must be 'Call 911 now'."""
        monkeypatch.delenv("EMERGENCY_NUMBER", raising=False)
        from app import get_button_spec
        spec = get_button_spec("emergency", self._make_result())
        assert spec["label"] == "Call 911 now"

    def test_emergency_button_url_is_tel_911_by_default(self, monkeypatch) -> None:
        """With EMERGENCY_NUMBER unset, button URL must be 'tel:911'."""
        monkeypatch.delenv("EMERGENCY_NUMBER", raising=False)
        from app import get_button_spec
        spec = get_button_spec("emergency", self._make_result())
        assert spec["url"] == "tel:911"

    def test_emergency_button_label_uses_configured_number(self, monkeypatch) -> None:
        monkeypatch.setenv("EMERGENCY_NUMBER", "999")
        from app import get_button_spec
        spec = get_button_spec("emergency", self._make_result())
        assert spec["label"] == "Call 999 now"
        assert spec["url"] == "tel:999"

    def test_emergency_button_kind_is_link(self, monkeypatch) -> None:
        monkeypatch.delenv("EMERGENCY_NUMBER", raising=False)
        from app import get_button_spec
        spec = get_button_spec("emergency", self._make_result())
        assert spec["kind"] == "link"
