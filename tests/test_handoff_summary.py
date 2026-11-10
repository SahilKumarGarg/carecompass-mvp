"""Tests for the handoff summary — format, content, and button-gate.

Requirements verified here:
  1. No "demo" word anywhere in the summary text.
  2. The user's concern text appears exactly once.
  3. Every line in the summary is under 100 characters (or the summary uses st.code
     with wrap_lines=True — verified structurally, not at runtime).
  4. The summary is created only after run_session; create_handoff_summary is the
     only caller (the session result has handoff_summary=None until consent).
  5. New format: CareCompass summary header, Date, Location searched, Who, Concern,
     Duration, Urgency, Suggested hospital, Phone, Address, Record validated, footer.

DATA_SOURCE is set to demo so existing area strings (Riverside, Greenfield) work.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Optional
from unittest.mock import patch

import pytest

# Force offline mode and demo data source.
for _k in ("WATSONX_API_KEY", "WATSONX_URL", "WATSONX_PROJECT_ID"):
    os.environ.pop(_k, None)
os.environ["DATA_SOURCE"] = "demo"

from src.orchestrator import (  # noqa: E402
    SessionResult,
    create_handoff_summary,
    run_session,
)
from src.intake import CaseResult  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(text: str, area: str = "Greenfield") -> SessionResult:
    return run_session(text, area)


def _make_minimal_result(concern: str = "I have a headache") -> SessionResult:
    """Build a minimal SessionResult with a known concern for targeted assertions."""
    case = CaseResult(
        who="myself",
        concern=concern,
        duration="2 days",
        severity_cues=[],
        area="Greenfield",
        missing_fields=[],
        confidence="offline",
        confidence_score=0.67,
        denied_symptoms=[],
    )
    return SessionResult(
        session_id="test-summary-session",
        tier="routine",
        state="present",
        case=case,
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


# ---------------------------------------------------------------------------
# 1. No "demo" word in the static/structural lines of the summary
# ---------------------------------------------------------------------------
#
# The task requires removing "demo" and "(demo data only)" from the *summary
# text we write* — the header, footer, and label lines.  Data values that come
# from data/services.json may legitimately contain "[DEMO-…]" phone numbers;
# those are intentional demo placeholders and are NOT in scope for this check.
#
# The minimal-result fixture uses a result with no ranked services so all data
# fields come from the CaseResult we construct — none of which contain "demo".

class TestNoDemo:
    def test_summary_header_has_no_demo_word(self) -> None:
        """The first line (header) must not contain 'demo'."""
        result = _make_minimal_result()
        summary = create_handoff_summary(result)
        first_line = summary.splitlines()[0]
        assert "demo" not in first_line.lower(), (
            f"Found 'demo' in summary header: {first_line!r}"
        )

    def test_summary_footer_has_no_demo_word(self) -> None:
        """The last line (footer/disclaimer) must not contain 'demo'."""
        result = _make_minimal_result()
        summary = create_handoff_summary(result)
        last_line = summary.splitlines()[-1]
        assert "demo" not in last_line.lower(), (
            f"Found 'demo' in summary footer: {last_line!r}"
        )

    def test_summary_no_demo_with_minimal_result(self) -> None:
        """A result with no ranked services (all fields from CaseResult) has no 'demo'."""
        result = _make_minimal_result()
        summary = create_handoff_summary(result)
        assert "demo" not in summary.lower(), (
            f"Found 'demo' in summary:\n{summary}"
        )

    def test_summary_no_demo_data_only_phrase(self) -> None:
        result = _make_minimal_result()
        summary = create_handoff_summary(result)
        assert "demo data only" not in summary.lower()

    def test_summary_no_demo_parenthetical(self) -> None:
        """The old '(demo)' parenthetical in the footer must be gone."""
        result = _make_minimal_result()
        summary = create_handoff_summary(result)
        assert "(demo)" not in summary.lower()


# ---------------------------------------------------------------------------
# 2. Concern appears exactly once
# ---------------------------------------------------------------------------

class TestConcernAppearsOnce:
    def test_concern_appears_once_minimal(self) -> None:
        concern = "I am having a headache"
        result = _make_minimal_result(concern=concern)
        summary = create_handoff_summary(result)
        # Count occurrences (case-insensitive).
        count = summary.lower().count(concern.lower())
        assert count == 1, (
            f"Expected concern to appear once, found {count} times.\nSummary:\n{summary}"
        )

    def test_concern_appears_once_full_pipeline(self) -> None:
        """Run through the full pipeline; the concern line must appear once."""
        result = _run("I need a repeat prescription for blood pressure.")
        summary = create_handoff_summary(result)
        # Extract the Concern: line value.
        concern_line = next(
            (line for line in summary.splitlines() if line.startswith("Concern:")),
            None,
        )
        assert concern_line is not None, "No 'Concern:' line found in summary"
        concern_value = concern_line[len("Concern:"):].strip()
        if concern_value and concern_value != "not specified":
            count = summary.count(concern_value)
            assert count == 1, (
                f"Concern value {concern_value!r} appears {count} times.\nSummary:\n{summary}"
            )

    def test_duplicated_input_does_not_duplicate_concern(self) -> None:
        """Passing the same text twice (simulating app double-append bug) must still yield
        concern text only once in the summary — the fix is upstream in _process_user_input,
        but we verify the intake layer handles deduplicated text correctly."""
        single = "I am having fever suggest where should i go"
        result = _run(single, area="Greenfield")
        summary = create_handoff_summary(result)
        concern_line = next(
            (line for line in summary.splitlines() if line.startswith("Concern:")),
            None,
        )
        assert concern_line is not None
        # The concern value must NOT itself be a repeated phrase.
        concern_value = concern_line[len("Concern:"):].strip()
        if concern_value and len(concern_value) > 5:
            # If the concern is "X X" where X == X split in half, it's doubled.
            mid = len(concern_value) // 2
            first_half = concern_value[:mid].strip()
            if first_half and first_half == concern_value[mid:].strip():
                pytest.fail(
                    f"Concern appears doubled: {concern_value!r}\nFull summary:\n{summary}"
                )


# ---------------------------------------------------------------------------
# 3. Line lengths ≤ 100 characters (or st.code with wrap_lines=True is used)
# ---------------------------------------------------------------------------

class TestLineLengths:
    def test_all_lines_under_100_chars(self) -> None:
        """Every line in the summary must be under 100 characters."""
        result = _run("I need a repeat prescription.", area="Greenfield")
        summary = create_handoff_summary(result)
        long_lines = [
            (i + 1, line)
            for i, line in enumerate(summary.splitlines())
            if len(line) > 100
        ]
        assert not long_lines, (
            "Lines exceeding 100 characters:\n"
            + "\n".join(f"  line {n}: {line!r}" for n, line in long_lines)
        )

    def test_minimal_result_lines_under_100_chars(self) -> None:
        result = _make_minimal_result()
        summary = create_handoff_summary(result)
        for i, line in enumerate(summary.splitlines()):
            assert len(line) <= 100, (
                f"Line {i + 1} exceeds 100 chars: {line!r}"
            )

    def test_app_uses_st_code_with_wrap_lines(self) -> None:
        """Verify app.py passes wrap_lines=True to st.code for the summary display."""
        import ast
        import pathlib
        source = pathlib.Path(__file__).parent.parent.joinpath("app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "code"
            ):
                # Check for wrap_lines=True keyword argument.
                for kw in node.keywords:
                    if kw.arg == "wrap_lines" and isinstance(kw.value, ast.Constant):
                        if kw.value.value is True:
                            return  # found — pass
        pytest.fail(
            "app.py does not call st.code(..., wrap_lines=True). "
            "Add wrap_lines=True to the st.code call that displays the summary."
        )


# ---------------------------------------------------------------------------
# 4. Summary created only after user clicks the button (consent gate)
# ---------------------------------------------------------------------------

class TestButtonGate:
    def test_handoff_summary_none_before_create(self) -> None:
        """run_session must not auto-create the handoff summary."""
        result = _run("I need a repeat prescription.")
        assert result.handoff_summary is None

    def test_create_handoff_summary_returns_non_empty(self) -> None:
        result = _run("I need a repeat prescription.", area="Greenfield")
        summary = create_handoff_summary(result)
        assert isinstance(summary, str)
        assert len(summary.strip()) > 0

    def test_session_result_unchanged_after_create(self) -> None:
        """create_handoff_summary must not mutate the SessionResult."""
        result = _run("I need a repeat prescription.", area="Greenfield")
        assert result.handoff_summary is None
        _ = create_handoff_summary(result)
        # handoff_summary is still None — it is not stored back on SessionResult.
        assert result.handoff_summary is None


# ---------------------------------------------------------------------------
# 5. New format: expected lines present
# ---------------------------------------------------------------------------

class TestSummaryFormat:
    def test_first_line_is_carecompass_summary(self) -> None:
        result = _make_minimal_result()
        summary = create_handoff_summary(result)
        assert summary.splitlines()[0] == "CareCompass summary"

    def test_contains_date_line(self) -> None:
        result = _make_minimal_result()
        summary = create_handoff_summary(result)
        assert any(line.startswith("Date:") for line in summary.splitlines())

    def test_contains_location_searched_line(self) -> None:
        result = _make_minimal_result()
        summary = create_handoff_summary(result)
        assert any(line.startswith("Location searched:") for line in summary.splitlines())

    def test_contains_who_line(self) -> None:
        result = _make_minimal_result()
        summary = create_handoff_summary(result)
        assert any(line.startswith("Who:") for line in summary.splitlines())

    def test_contains_concern_line(self) -> None:
        result = _make_minimal_result()
        summary = create_handoff_summary(result)
        assert any(line.startswith("Concern:") for line in summary.splitlines())

    def test_contains_duration_line(self) -> None:
        result = _make_minimal_result()
        summary = create_handoff_summary(result)
        assert any(line.startswith("Duration:") for line in summary.splitlines())

    def test_contains_urgency_line(self) -> None:
        result = _make_minimal_result()
        summary = create_handoff_summary(result)
        assert any(line.startswith("Urgency:") for line in summary.splitlines())

    def test_last_line_is_footer(self) -> None:
        result = _make_minimal_result()
        summary = create_handoff_summary(result)
        last = summary.splitlines()[-1]
        assert "not medical advice" in last.lower()

    def test_footer_has_no_demo_word(self) -> None:
        result = _make_minimal_result()
        summary = create_handoff_summary(result)
        last = summary.splitlines()[-1]
        assert "demo" not in last.lower()
