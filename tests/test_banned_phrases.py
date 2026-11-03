"""Tests for banned-phrase detection in src/checks.py.

Verifies that:
- Diagnosis-style sentences are BLOCKED (at least 10).
- Safe template/answer sentences PASS (at least 10).
- The exact urgent answer text for SCN005–SCN017 passes final_check.

No real patient data. All text is synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.checks import _check_banned_phrases, final_check

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SERVICES_PATH = Path(__file__).parent.parent / "data" / "services.json"
_SERVICE_INDEX: dict[str, dict] = {
    s["id"]: s
    for s in json.loads(_SERVICES_PATH.read_text(encoding="utf-8"))["services"]
}


def _answer_for(service_id: str, extra: str = "") -> str:
    """Build a minimal passing answer text for a known service."""
    svc = _SERVICE_INDEX[service_id]
    return (
        f"{svc['name']} ({service_id})  "
        f"Phone: {svc['phone']}  "
        f"Data last verified: {svc['last_verified']}  "
        f"{extra}"
    )


# ---------------------------------------------------------------------------
# 10 diagnosis-style sentences that MUST be BLOCKED
# ---------------------------------------------------------------------------

BLOCKED_SENTENCES = [
    # BP001 — diagnosis phrases
    "Based on your history, you probably have appendicitis.",
    "After reviewing the results, you likely have a serious infection.",
    "You may have type 2 diabetes — please see your GP.",
    "You might have a kidney stone causing this discomfort.",
    "It sounds like you've got a respiratory infection.",
    "You are suffering from chronic fatigue syndrome.",
    "This is a case of viral gastroenteritis.",
    "Your diagnosis is hypertension.",
    "The diagnosis is consistent with pneumonia.",
    "Your condition is most likely irritable bowel syndrome.",
    # BP002 — treatment/medication phrases
    "Take 500 mg of paracetamol every four hours.",
    "I recommend taking a course of antibiotics.",
    "You should take this medication with food.",
    # BP003 — condition naming
    "Signs of meningitis include a stiff neck and light sensitivity.",
]


@pytest.mark.parametrize("sentence", BLOCKED_SENTENCES)
def test_blocked_sentence_is_detected(sentence: str) -> None:
    """Every diagnosis-style sentence must be caught by _check_banned_phrases."""
    problems = _check_banned_phrases(sentence)
    assert problems, (
        f"Expected at least one banned-phrase problem for: {sentence!r}\n"
        f"Got: {problems}"
    )


# ---------------------------------------------------------------------------
# 10 safe sentences that MUST PASS
# ---------------------------------------------------------------------------

SAFE_SENTENCES = [
    # Normal template / service-recommendation language
    "These symptoms need attention today.",
    "The symptoms described need attention today.",
    "Please contact an urgent care centre for a same-day appointment.",
    "If symptoms get significantly worse, call your emergency number straight away.",
    "Please call them today to arrange an appointment.",
    "The service is approximately 1.2 km away and is open 24 hours.",
    "Services include: GP appointments, minor illness advice, blood pressure check.",
    "If calling is not possible, ask anyone nearby to help.",
    "Do not wait more than a few hours — contact the centre today.",
    # Second-person wording that is NOT a diagnosis
    "If symptoms get worse, seek help immediately.",
    "Please describe the concern in more detail so we can help.",
    "Contact Greenfield Family Practice today for a same-day appointment.",
    "Call [DEMO-0102] to speak with the practice.",
]


@pytest.mark.parametrize("sentence", SAFE_SENTENCES)
def test_safe_sentence_passes(sentence: str) -> None:
    """Safe template/advisory sentences must not trigger any banned-phrase problem."""
    problems = _check_banned_phrases(sentence)
    assert problems == [], (
        f"Expected no problems for safe sentence: {sentence!r}\n"
        f"Got: {problems}"
    )


# ---------------------------------------------------------------------------
# SCN005–SCN017: urgent answer text must pass final_check
#
# We construct the exact answer text that _build_answer_text() produces for
# a minimal urgent answer: the urgent heading + body (from messages.json)
# prepended before service lines.  We test the heading/body portion against
# _check_banned_phrases directly, then test a complete answer through
# final_check so that every check is exercised together.
# ---------------------------------------------------------------------------

_MESSAGES_PATH = Path(__file__).parent.parent / "data" / "messages.json"
_MESSAGES = json.loads(_MESSAGES_PATH.read_text(encoding="utf-8"))["messages"]

_URGENT_HEADING = _MESSAGES["urgent"]["heading"]
_URGENT_BODY = (
    _MESSAGES["urgent"]["body"]
    .replace("{emergency_number}", "[DEMO-999]")
    .replace("{emergency_region}", "DEMO REGION")
)


def test_urgent_heading_passes_banned_check() -> None:
    """The urgent heading must not contain any banned phrase."""
    problems = _check_banned_phrases(_URGENT_HEADING)
    assert problems == [], f"Urgent heading triggered banned phrase: {problems}"


def test_urgent_body_passes_banned_check() -> None:
    """The urgent body (with placeholders resolved) must not contain any banned phrase."""
    problems = _check_banned_phrases(_URGENT_BODY)
    assert problems == [], f"Urgent body triggered banned phrase: {problems}"


# SCN005 — Northgate, specialist (SVC014)
def test_scn005_urgent_answer_passes() -> None:
    svc_id = "SVC014"
    answer = (
        f"{_URGENT_HEADING}\n{_URGENT_BODY}\n\n"
        + _answer_for(svc_id, "Services include: IAPT talking therapies, crisis assessment.")
    )
    result = final_check(answer, [svc_id], tier="urgent")
    assert result.passed, f"SCN005 answer failed final_check: {result.problems}"


# SCN006 — Eastfield, hospital_emergency (SVC018)
def test_scn006_urgent_answer_passes() -> None:
    svc_id = "SVC018"
    answer = (
        f"{_URGENT_HEADING}\n{_URGENT_BODY}\n\n"
        + _answer_for(svc_id, "Open 24 hours. Call today.")
    )
    result = final_check(answer, [svc_id], tier="urgent")
    assert result.passed, f"SCN006 answer failed final_check: {result.problems}"


# SCN007 — Greenfield, primary_care (SVC002)
def test_scn007_urgent_answer_passes() -> None:
    svc_id = "SVC002"
    answer = (
        f"{_URGENT_HEADING}\n{_URGENT_BODY}\n\n"
        + _answer_for(svc_id, "GP appointments available. Call today.")
    )
    result = final_check(answer, [svc_id], tier="urgent")
    assert result.passed, f"SCN007 answer failed final_check: {result.problems}"


# SCN008 — Riverside, hospital_emergency (SVC008)
def test_scn008_urgent_answer_passes() -> None:
    svc_id = "SVC008"
    answer = (
        f"{_URGENT_HEADING}\n{_URGENT_BODY}\n\n"
        + _answer_for(svc_id, "Open 24 hours. Emergency care available today.")
    )
    result = final_check(answer, [svc_id], tier="urgent")
    assert result.passed, f"SCN008 answer failed final_check: {result.problems}"


# SCN013 — Greenfield, hospital_emergency (SVC003)
def test_scn013_urgent_answer_passes() -> None:
    svc_id = "SVC003"
    answer = (
        f"{_URGENT_HEADING}\n{_URGENT_BODY}\n\n"
        + _answer_for(svc_id, "Urgent walk-in care available. Attend today.")
    )
    result = final_check(answer, [svc_id], tier="urgent")
    assert result.passed, f"SCN013 answer failed final_check: {result.problems}"


# SCN014 — Riverside, specialist (SVC009 is cardiology; closest specialist is
#           actually SVC009 but mental health scenarios route to whichever
#           specialist the finder returns — use SVC009 to test the pattern)
def test_scn014_urgent_answer_passes() -> None:
    # Riverside has SVC009 (Riverside Cardiology Outpatients) as specialist;
    # mental health in Riverside would go to SVC007 (primary_care) or SVC010
    # (telemedicine). We use SVC008 (open 24h A&E) as a safe urgent option.
    svc_id = "SVC008"
    answer = (
        f"{_URGENT_HEADING}\n{_URGENT_BODY}\n\n"
        + _answer_for(svc_id, "Open 24 hours. Urgent care available today.")
    )
    result = final_check(answer, [svc_id], tier="urgent")
    assert result.passed, f"SCN014 answer failed final_check: {result.problems}"


# SCN015 — Westbury, hospital_emergency
def test_scn015_urgent_answer_passes() -> None:
    # Westbury has SVC019 (pharmacy), SVC020 (primary_care); no hospital_emergency.
    # The finder falls back to the nearest available service.  Use SVC020.
    svc_id = "SVC020"
    answer = (
        f"{_URGENT_HEADING}\n{_URGENT_BODY}\n\n"
        + _answer_for(svc_id, "Same-day GP appointments available. Contact today.")
    )
    result = final_check(answer, [svc_id], tier="urgent")
    assert result.passed, f"SCN015 answer failed final_check: {result.problems}"


# SCN016 — Northgate, specialist (SVC014)
def test_scn016_urgent_answer_passes() -> None:
    svc_id = "SVC014"
    answer = (
        f"{_URGENT_HEADING}\n{_URGENT_BODY}\n\n"
        + _answer_for(svc_id, "Crisis assessment available. Call today.")
    )
    result = final_check(answer, [svc_id], tier="urgent")
    assert result.passed, f"SCN016 answer failed final_check: {result.problems}"


# SCN017 — Eastfield, hospital_emergency (SVC018)
def test_scn017_urgent_answer_passes() -> None:
    svc_id = "SVC018"
    answer = (
        f"{_URGENT_HEADING}\n{_URGENT_BODY}\n\n"
        + _answer_for(svc_id, "Open 24 hours. Acute medicine. Attend today.")
    )
    result = final_check(answer, [svc_id], tier="urgent")
    assert result.passed, f"SCN017 answer failed final_check: {result.problems}"
