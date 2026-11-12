"""Tests for src/orchestrator.py — no Streamlit, no LLM, no network.

These tests run under DATA_SOURCE=demo so that area strings like "Riverside"
are valid (they are named areas in data/services.json). The hospitals_us path
is validated by test_locations.py and test_benchmark.py.
"""

from __future__ import annotations

import json
import os

import pytest

# Ensure offline mode.
for _k in ("WATSONX_API_KEY", "WATSONX_URL", "WATSONX_PROJECT_ID"):
    os.environ.pop(_k, None)

# Use demo data source so existing area strings (Riverside, Greenfield etc.) work.
os.environ["DATA_SOURCE"] = "demo"

from src.orchestrator import (  # noqa: E402
    SessionResult,
    _DEMO_EMERGENCY_NUMBER,
    _DEMO_EMERGENCY_REGION,
    _emergency_number,
    _emergency_region,
    clear_cache as _clear_messages_cache,
    create_handoff_summary,
    run_session,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(text: str, area: str = "Riverside") -> SessionResult:
    return run_session(text, area)


# ---------------------------------------------------------------------------
# Return type and schema
# ---------------------------------------------------------------------------

class TestSessionResultSchema:
    def test_returns_session_result(self) -> None:
        result = _run("I need a repeat prescription.")
        assert isinstance(result, SessionResult)

    def test_session_id_is_string(self) -> None:
        result = _run("I need a repeat prescription.")
        assert isinstance(result.session_id, str)
        assert len(result.session_id) > 0

    def test_tier_is_valid(self) -> None:
        result = _run("I need a repeat prescription.")
        assert result.tier in {"emergency", "urgent", "routine"}

    def test_answer_text_is_non_empty(self) -> None:
        result = _run("I need a repeat prescription.")
        assert isinstance(result.answer_text, str)
        assert len(result.answer_text) > 0

    def test_handoff_summary_none_before_consent(self) -> None:
        result = _run("I need a repeat prescription.")
        assert result.handoff_summary is None

    def test_candidates_is_list(self) -> None:
        result = _run("I need a repeat prescription.")
        assert isinstance(result.candidates, list)

    def test_ranked_is_list(self) -> None:
        result = _run("I need a repeat prescription.")
        assert isinstance(result.ranked, list)


# ---------------------------------------------------------------------------
# Emergency path
# ---------------------------------------------------------------------------

class TestEmergencyPath:
    def test_chest_pain_returns_emergency_tier(self) -> None:
        result = _run(
            "My dad is clutching his chest and saying it feels like something is crushing him.",
            area="Riverside",
        )
        assert result.tier == "emergency"

    def test_emergency_state(self) -> None:
        result = _run("My mum's face is drooping and her speech is slurred.")
        assert result.state == "emergency"

    def test_emergency_answer_contains_heading(self) -> None:
        result = _run("My mum's face is drooping and her speech is slurred.")
        assert "emergency" in result.answer_text.lower()

    def test_emergency_no_candidates(self) -> None:
        """Emergency path must skip service search entirely."""
        result = _run("My mum's face is drooping and her speech is slurred.")
        assert result.candidates == []

    def test_emergency_no_ranked(self) -> None:
        result = _run("My mum's face is drooping and her speech is slurred.")
        assert result.ranked == []

    def test_emergency_check_passed_true(self) -> None:
        """Emergency bypasses final_check; check_passed is set True."""
        result = _run("My mum's face is drooping and her speech is slurred.")
        assert result.check_passed is True

    def test_emergency_needs_human_review_false(self) -> None:
        result = _run("My mum's face is drooping and her speech is slurred.")
        assert result.needs_human_review is False

    def test_emergency_answer_contains_emergency_number(self) -> None:
        # Set a recognisable test value; verify it appears in the answer.
        os.environ["EMERGENCY_NUMBER"] = "555-DEMO"
        _clear_messages_cache()   # invalidate the pre-loaded cache so new env is picked up
        result = _run("I ate a peanut and my throat is swelling.")
        assert "555-DEMO" in result.answer_text
        os.environ.pop("EMERGENCY_NUMBER", None)   # restore so other tests see the default
        _clear_messages_cache()   # restore cache to default values for subsequent tests

    def test_emergency_no_diagnosis_in_answer(self) -> None:
        result = _run("My dad is clutching his chest and crushing him pain.")
        lower = result.answer_text.lower()
        # "you have described" is fine; only diagnosis-form phrases are banned.
        assert "you have a " not in lower
        assert "you have an " not in lower
        assert "diagnosed with" not in lower


# ---------------------------------------------------------------------------
# Routine path
# ---------------------------------------------------------------------------

class TestRoutinePath:
    def test_routine_prescription_tier(self) -> None:
        result = _run("I need to request a repeat prescription for my blood pressure tablets.")
        assert result.tier == "routine"

    def test_routine_state_is_present_or_review(self) -> None:
        result = _run("I need to request a repeat prescription.", area="Westbury")
        assert result.state in {"present", "needs_human_review"}

    def test_routine_has_candidates(self) -> None:
        result = _run("I need to request a repeat prescription.", area="Greenfield")
        assert len(result.candidates) > 0

    def test_routine_ranked_at_most_four(self) -> None:
        result = _run("I need to request a repeat prescription.", area="Greenfield")
        assert len(result.ranked) <= 4

    def test_answer_does_not_contain_diagnosis(self) -> None:
        result = _run("I have had a mild rash on my arm for a week.")
        lower = result.answer_text.lower()
        banned = ["you have", "take ibuprofen", "take paracetamol", "appendicitis"]
        for phrase in banned:
            assert phrase not in lower, f"Banned phrase found: {phrase!r}"

    def test_answer_contains_last_verified_when_ranked(self) -> None:
        result = _run("I need a repeat prescription.", area="Greenfield")
        if result.ranked and not result.needs_human_review:
            top = result.ranked[0]
            # demo path uses .service; hospitals_us path uses .hospital
            svc = getattr(top, "service", None) or getattr(top, "hospital", None)
            val = getattr(svc, "last_verified", None) or getattr(svc, "val_year", None)
            if val:
                assert val in result.answer_text


# ---------------------------------------------------------------------------
# Urgent path
# ---------------------------------------------------------------------------

class TestUrgentPath:
    def test_urgent_self_harm_returns_at_least_urgent(self) -> None:
        result = _run(
            "I've been having thoughts of hurting myself lately.",
            area="Northgate",
        )
        assert result.tier in {"urgent", "emergency"}

    def test_urgent_answer_contains_urgent_language(self) -> None:
        result = _run(
            "I've been having thoughts of hurting myself lately.",
            area="Northgate",
        )
        if result.tier == "urgent" and not result.needs_human_review:
            lower = result.answer_text.lower()
            markers = ["today", "as soon as", "do not wait", "call", "urgently"]
            assert any(m in lower for m in markers)


# ---------------------------------------------------------------------------
# Final-check retry behaviour
# ---------------------------------------------------------------------------

class TestFinalCheckRetry:
    def test_check_passed_or_human_review_set(self) -> None:
        """After run_session, exactly one of check_passed or needs_human_review is True."""
        result = _run("I have a mild headache.", area="Eastfield")
        assert result.check_passed or result.needs_human_review

    def test_needs_human_review_answer_text(self) -> None:
        """If needs_human_review, the answer mentions it."""
        result = _run("I have a mild headache.", area="Eastfield")
        if result.needs_human_review:
            lower = result.answer_text.lower()
            assert "review" in lower or "confirm" in lower or "gp" in lower


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class TestAuditLog:
    def test_audit_file_created(self, tmp_path, monkeypatch) -> None:
        """Audit log is written after a session."""
        import src.audit as _audit
        monkeypatch.setattr(_audit, "_LOGS_DIR", tmp_path)
        monkeypatch.setattr(_audit, "_AUDIT_FILE", tmp_path / "audit.jsonl")
        _run("I need a repeat prescription.")
        log_file = tmp_path / "audit.jsonl"
        assert log_file.exists()
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) >= 3  # at minimum: describe, understand, safety

    def test_audit_lines_are_valid_json(self, tmp_path, monkeypatch) -> None:
        import src.audit as _audit
        monkeypatch.setattr(_audit, "_LOGS_DIR", tmp_path)
        monkeypatch.setattr(_audit, "_AUDIT_FILE", tmp_path / "audit.jsonl")
        _run("I need a repeat prescription.")
        log_file = tmp_path / "audit.jsonl"
        for line in log_file.read_text().strip().splitlines():
            record = json.loads(line)  # must not raise
            assert "ts" in record
            assert "step" in record
            assert "session_id" in record

    def test_audit_emergency_records_emergency_exit(self, tmp_path, monkeypatch) -> None:
        import src.audit as _audit
        monkeypatch.setattr(_audit, "_LOGS_DIR", tmp_path)
        monkeypatch.setattr(_audit, "_AUDIT_FILE", tmp_path / "audit.jsonl")
        _run("My mum's face is drooping and her speech is slurred.")
        log_file = tmp_path / "audit.jsonl"
        steps = [json.loads(l)["step"] for l in log_file.read_text().strip().splitlines()]
        assert "emergency_exit" in steps


# ---------------------------------------------------------------------------
# Handoff summary
# ---------------------------------------------------------------------------

class TestHandoffSummary:
    def test_create_handoff_returns_string(self) -> None:
        result = _run("I need a repeat prescription.", area="Greenfield")
        summary = create_handoff_summary(result)
        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_handoff_contains_tier(self) -> None:
        result = _run("I need a repeat prescription.", area="Greenfield")
        summary = create_handoff_summary(result)
        assert result.tier in summary

    def test_handoff_contains_not_medical_advice(self) -> None:
        result = _run("I need a repeat prescription.", area="Greenfield")
        summary = create_handoff_summary(result)
        assert "not medical advice" in summary.lower()

    def test_handoff_summary_not_set_on_session_result(self) -> None:
        """run_session must NOT auto-create the handoff summary."""
        result = _run("I need a repeat prescription.")
        assert result.handoff_summary is None

    def test_session_id_consistent_across_call(self) -> None:
        result = _run("I need a repeat prescription.")
        sid = result.session_id
        summary = create_handoff_summary(result)
        assert isinstance(summary, str)
        # session_id hasn't changed
        assert result.session_id == sid


# ---------------------------------------------------------------------------
# Emergency number and region configuration
# ---------------------------------------------------------------------------

class TestEmergencyConfig:
    def setup_method(self) -> None:
        """Ensure env vars are cleared before each test."""
        os.environ.pop("EMERGENCY_NUMBER", None)
        os.environ.pop("EMERGENCY_REGION", None)

    def teardown_method(self) -> None:
        os.environ.pop("EMERGENCY_NUMBER", None)
        os.environ.pop("EMERGENCY_REGION", None)

    def test_demo_default_number_when_env_unset(self) -> None:
        assert _emergency_number() == _DEMO_EMERGENCY_NUMBER

    def test_demo_default_region_when_env_unset(self) -> None:
        assert _emergency_region() == _DEMO_EMERGENCY_REGION

    def test_custom_number_read_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("EMERGENCY_NUMBER", "112")
        assert _emergency_number() == "112"

    def test_custom_region_read_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("EMERGENCY_REGION", "UK")
        assert _emergency_region() == "UK"

    def test_blank_env_falls_back_to_demo_number(self, monkeypatch) -> None:
        monkeypatch.setenv("EMERGENCY_NUMBER", "   ")
        assert _emergency_number() == _DEMO_EMERGENCY_NUMBER

    def test_blank_env_falls_back_to_demo_region(self, monkeypatch) -> None:
        monkeypatch.setenv("EMERGENCY_REGION", "")
        assert _emergency_region() == _DEMO_EMERGENCY_REGION

    def test_demo_default_number_is_911(self) -> None:
        """Code default (when EMERGENCY_NUMBER is unset) must be '911'.
        The old [DEMO-999] sentinel is retired; 911 is the documented default
        in .env.example and is a safe, widely-recognisable emergency number."""
        assert _DEMO_EMERGENCY_NUMBER == "911"

    def test_demo_region_contains_demo_label(self) -> None:
        assert "DEMO" in _DEMO_EMERGENCY_REGION or "demo" in _DEMO_EMERGENCY_REGION.lower()

    def test_emergency_answer_shows_demo_number_when_env_unset(self) -> None:
        result = _run("I ate a peanut and my throat is swelling.")
        assert _DEMO_EMERGENCY_NUMBER in result.answer_text

    def test_emergency_answer_shows_configured_number(self, monkeypatch) -> None:
        monkeypatch.setenv("EMERGENCY_NUMBER", "911")
        result = _run("I ate a peanut and my throat is swelling.")
        assert "911" in result.answer_text

    def test_urgent_answer_shows_emergency_region(self, monkeypatch) -> None:
        monkeypatch.setenv("EMERGENCY_REGION", "TestLand")
        _clear_messages_cache()   # invalidate so the new env value is picked up
        try:
            result = _run(
                "I've been having thoughts of hurting myself lately.",
                area="Northgate",
            )
            # Region appears in the urgent body message only on non-review paths.
            if result.tier == "urgent" and not result.needs_human_review:
                assert "TestLand" in result.answer_text
        finally:
            _clear_messages_cache()   # restore defaults for subsequent tests

    def test_no_hardcoded_bare_999_in_answer_when_env_unset(self) -> None:
        """When EMERGENCY_NUMBER is not set, a bare '999' (not wrapped in the demo label)
        must NOT appear — the demo default [DEMO-999] is acceptable."""
        result = _run("I ate a peanut and my throat is swelling.")
        # The demo default [DEMO-999] is fine; a naked '999' would mean a hardcode slipped through.
        # We verify the demo label is present and the answer is not bare '999'.
        assert _DEMO_EMERGENCY_NUMBER in result.answer_text
        # Check that '999' does not appear outside of the demo label.
        import re as _re
        bare = _re.sub(_re.escape(_DEMO_EMERGENCY_NUMBER), "", result.answer_text)
        assert "999" not in bare


# ---------------------------------------------------------------------------
# needs_human_review path — urgent/emergency vs routine branching
# ---------------------------------------------------------------------------

class TestNeedsHumanReviewBranching:
    """Verify that persistent final_check failure shows the right message per tier."""

    def _always_fail(self):
        """Return a CheckResult factory that always reports a failure."""
        from src.checks import CheckResult
        def _fail(*args, **kwargs):
            return CheckResult(passed=False, problems=["forced failure for test"])
        return _fail

    def test_urgent_review_shows_tier_badge(self, monkeypatch) -> None:
        """When final_check always fails on an urgent session, the answer must contain [URGENT]."""
        monkeypatch.setattr("src.orchestrator.final_check", self._always_fail())
        result = _run(
            "I've been having thoughts of hurting myself lately.",
            area="Northgate",
        )
        if result.tier == "urgent":
            assert result.needs_human_review is True
            assert "[URGENT]" in result.answer_text

    def test_urgent_review_shows_emergency_number(self, monkeypatch) -> None:
        """Urgent review path must include the emergency number."""
        monkeypatch.setattr("src.orchestrator.final_check", self._always_fail())
        result = _run(
            "I've been having thoughts of hurting myself lately.",
            area="Northgate",
        )
        if result.tier == "urgent" and result.needs_human_review:
            assert _DEMO_EMERGENCY_NUMBER in result.answer_text

    def test_urgent_review_says_do_not_wait(self, monkeypatch) -> None:
        """Urgent review path must contain 'Do not wait'."""
        monkeypatch.setattr("src.orchestrator.final_check", self._always_fail())
        result = _run(
            "I've been having thoughts of hurting myself lately.",
            area="Northgate",
        )
        if result.tier == "urgent" and result.needs_human_review:
            assert "do not wait" in result.answer_text.lower()

    def test_urgent_review_offers_handoff(self, monkeypatch) -> None:
        """Urgent review path must mention the handoff summary offer."""
        monkeypatch.setattr("src.orchestrator.final_check", self._always_fail())
        result = _run(
            "I've been having thoughts of hurting myself lately.",
            area="Northgate",
        )
        if result.tier == "urgent" and result.needs_human_review:
            lower = result.answer_text.lower()
            assert "summary" in lower or "share" in lower or "professional" in lower

    def test_urgent_review_shows_rule_reason(self, monkeypatch) -> None:
        """Urgent review must include at least one rule hit name (badge reason)."""
        monkeypatch.setattr("src.orchestrator.final_check", self._always_fail())
        result = _run(
            "I've been having thoughts of hurting myself lately.",
            area="Northgate",
        )
        if result.tier == "urgent" and result.needs_human_review:
            # RF009 fires here; name is "self_harm_thoughts" → displayed as "self harm thoughts"
            assert "self harm thoughts" in result.answer_text.lower()

    def test_urgent_review_does_not_show_generic_heading(self, monkeypatch) -> None:
        """Urgent review must NOT show the routine 'We could not confirm a match.' heading."""
        monkeypatch.setattr("src.orchestrator.final_check", self._always_fail())
        result = _run(
            "I've been having thoughts of hurting myself lately.",
            area="Northgate",
        )
        if result.tier == "urgent" and result.needs_human_review:
            assert "we could not confirm a match" not in result.answer_text.lower()

    def test_routine_review_shows_generic_heading(self, monkeypatch) -> None:
        """When final_check always fails on a routine session, the generic heading appears."""
        monkeypatch.setattr("src.orchestrator.final_check", self._always_fail())
        result = _run(
            "I need to request a repeat prescription.",
            area="Westbury",
        )
        if result.tier == "routine" and result.needs_human_review:
            assert "we could not confirm a match" in result.answer_text.lower()

    def test_routine_review_does_not_show_tier_badge(self, monkeypatch) -> None:
        """Routine review must NOT show an [URGENT] or [EMERGENCY] badge."""
        monkeypatch.setattr("src.orchestrator.final_check", self._always_fail())
        result = _run(
            "I need to request a repeat prescription.",
            area="Westbury",
        )
        if result.tier == "routine" and result.needs_human_review:
            assert "[URGENT]" not in result.answer_text
            assert "[EMERGENCY]" not in result.answer_text

    def test_needs_review_true_when_always_failing(self, monkeypatch) -> None:
        """Patching final_check to always fail must produce needs_human_review=True."""
        monkeypatch.setattr("src.orchestrator.final_check", self._always_fail())
        result = _run("I need a repeat prescription.", area="Greenfield")
        assert result.needs_human_review is True

    def test_check_passed_false_when_always_failing(self, monkeypatch) -> None:
        """When final_check always fails, check_passed must be False."""
        monkeypatch.setattr("src.orchestrator.final_check", self._always_fail())
        result = _run("I need a repeat prescription.", area="Greenfield")
        assert result.check_passed is False

    def test_urgent_review_emergency_number_configurable(self, monkeypatch) -> None:
        """The emergency number in the urgent review path respects EMERGENCY_NUMBER env var."""
        monkeypatch.setattr("src.orchestrator.final_check", self._always_fail())
        monkeypatch.setenv("EMERGENCY_NUMBER", "112")
        _clear_messages_cache()   # invalidate so the new env value is picked up
        try:
            result = _run(
                "My partner has a very high temperature and is confused.",
                area="Eastfield",
            )
            if result.tier in ("urgent", "emergency") and result.needs_human_review:
                assert "112" in result.answer_text
        finally:
            _clear_messages_cache()   # restore defaults for subsequent tests

    def test_no_hits_urgent_review_shows_fallback_reason(self, monkeypatch) -> None:
        """When hits list is empty on an urgent review, fallback reason text is shown."""
        from src.checks import CheckResult
        from src.safety import SafetyResult

        monkeypatch.setattr("src.orchestrator.final_check", self._always_fail())
        # Force check_red_flags to return urgent with no hits.
        monkeypatch.setattr(
            "src.orchestrator.check_red_flags",
            lambda _text: SafetyResult(tier="urgent", hits=[]),
        )
        result = _run("Some vague concern.", area="Greenfield")
        if result.needs_human_review:
            assert "symptoms described" in result.answer_text.lower()
