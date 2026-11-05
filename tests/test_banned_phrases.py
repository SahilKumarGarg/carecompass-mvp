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
# Force reload of the module-level cache so changes to banned_phrases.json
# are always picked up within a test run.
# ---------------------------------------------------------------------------

import src.checks as _checks_module
_checks_module._banned_cache = None   # reset so next call re-reads the file

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SERVICES_PATH = Path(__file__).parent.parent / "data" / "services.json"
_SERVICE_INDEX: dict[str, dict] = {
    s["id"]: s
    for s in json.loads(_SERVICES_PATH.read_text(encoding="utf-8"))["services"]
}

_MESSAGES_PATH = Path(__file__).parent.parent / "data" / "messages.json"
_MESSAGES = json.loads(_MESSAGES_PATH.read_text(encoding="utf-8"))["messages"]

_URGENT_HEADING = _MESSAGES["urgent"]["heading"]
_URGENT_BODY = (
    _MESSAGES["urgent"]["body"]
    .replace("{emergency_number}", "[DEMO-999]")
    .replace("{emergency_region}", "DEMO REGION")
)
_URGENT_ESCALATION = (
    _MESSAGES["urgent"]["escalation_prompt"]
    .replace("{emergency_number}", "[DEMO-999]")
    .replace("{emergency_region}", "DEMO REGION")
)


def _urgent_prefix() -> str:
    """Return the full urgent message block prepended to every urgent answer."""
    return f"{_URGENT_HEADING}\n{_URGENT_BODY}\n{_URGENT_ESCALATION}\n\n"


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
# 10+ diagnosis-style sentences that MUST be BLOCKED
# ---------------------------------------------------------------------------

BLOCKED_SENTENCES = [
    # BP001 — broad diagnosis patterns (user-specified)
    "Based on your history, you probably have appendicitis.",
    "After reviewing the results, you likely have a serious infection.",
    "You may have type 2 diabetes — please see your GP.",
    "You might have a kidney stone causing this discomfort.",
    "It sounds like you've got a respiratory infection.",
    "You are suffering from chronic fatigue syndrome.",
    "This is a case of viral gastroenteritis.",
    "Your diagnosis is hypertension.",
    "Your diagnosis is consistent with pneumonia.",
    "Your condition is most likely irritable bowel syndrome.",
    "This sounds like a migraine.",
    "This is likely a chest infection.",
    "This is probably a viral illness.",
    "The patient has been diagnosed with appendicitis.",
    "The diagnosis is myocardial infarction.",
    "The diagnosis is consistent with sepsis.",
    "This is a diagnosis of deep vein thrombosis.",
    # BP002 — treatment/medication patterns (user-specified)
    "Take 500 mg of paracetamol every four hours.",
    "I recommend taking a course of antibiotics.",
    "You should take this medication with food.",
    # BP003 — condition naming
    "Signs of meningitis include a stiff neck and light sensitivity.",
    "The presentation is consistent with pulmonary embolism.",
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
# 10+ safe sentences that MUST PASS
# ---------------------------------------------------------------------------

SAFE_SENTENCES = [
    # Urgent template language — third-person, no diagnosis
    "These symptoms need attention today.",
    "The symptoms described need attention today.",
    "Please contact an urgent care centre for a same-day appointment.",
    "If symptoms get significantly worse, call your emergency number straight away.",
    "If symptoms worsen quickly, do not wait — call [DEMO-999].",
    # Third-person escalation phrasing (matches escalation_prompt template)
    "If any of the following appear — difficulty breathing, chest pain, loss of consciousness — call [DEMO-999] straight away.",
    # Service recommendation language
    "Please call them today to arrange an appointment.",
    "The service is approximately 1.2 km away and is open 24 hours.",
    "Services include: GP appointments, minor illness advice, blood pressure check.",
    "If calling is not possible, ask anyone nearby to help.",
    "Do not wait more than a few hours — contact the centre today.",
    # Advisory second-person that is NOT a diagnosis
    "If symptoms get worse, seek help immediately.",
    "Please describe the concern in more detail so we can help.",
    "Contact Greenfield Family Practice today for a same-day appointment.",
    "Call [DEMO-0102] to speak with the practice.",
    # Phrasing that contains 'you' but is not diagnostic
    "Please let us know if you need further help.",
    "A clinician will be able to advise you further.",
    "It is open 24 hours and you can walk in at any time.",
    "Thank you for using CareCompass.",
    "If you are unable to travel, a telemedicine option is available.",
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
# BP004 — "you have <condition>" group: must be BLOCKED
# ---------------------------------------------------------------------------

YOU_HAVE_CONDITION_BLOCKED = [
    "You have pneumonia.",
    "You have an infection.",
    "You have a stroke.",
    "You have appendicitis.",
    "You have diabetes.",
    "You have a heart condition.",
    "You have asthma.",
    "You have cancer.",
    "You have flu.",
    "You have covid.",
    "You have a chest infection.",
    "You have high blood pressure.",
    "You've got an infection.",
    "You've got a heart condition.",
    "You've got diabetes.",
    "YOU HAVE AN INFECTION.",          # case-insensitive
    "it turns out you have a stroke.", # embedded in a sentence
]


@pytest.mark.parametrize("sentence", YOU_HAVE_CONDITION_BLOCKED)
def test_you_have_condition_is_blocked(sentence: str) -> None:
    """'you have <condition>' diagnosis sentences must be caught by _check_banned_phrases."""
    problems = _check_banned_phrases(sentence)
    assert problems, (
        f"Expected 'you have <condition>' to be blocked: {sentence!r}\n"
        f"Got: {problems}"
    )


# ---------------------------------------------------------------------------
# BP004 — advisory "you have" sentences: must NOT be blocked
# ---------------------------------------------------------------------------

YOU_HAVE_ADVISORY_SAFE = [
    "If you have chest pain, call emergency services.",
    "Get help if you have trouble breathing.",
    "Tell the clinic if you have any allergies.",
    "If you have questions, please contact your GP.",
    "Let us know if you have difficulty travelling.",
    "Call 999 if you have a seizure.",
    "Seek help if you have a reaction to any medication.",
    "Please attend even if you have mild symptoms.",
    "If you have not improved after 48 hours, see a GP.",
    "Check with the practice if you have specific access needs.",
]


@pytest.mark.parametrize("sentence", YOU_HAVE_ADVISORY_SAFE)
def test_you_have_advisory_is_not_blocked(sentence: str) -> None:
    """Advisory sentences containing 'you have' must not be flagged as banned phrases."""
    problems = _check_banned_phrases(sentence)
    assert problems == [], (
        f"Expected advisory 'you have' sentence to pass: {sentence!r}\n"
        f"Got: {problems}"
    )


# ---------------------------------------------------------------------------
# Urgent message template self-checks
# ---------------------------------------------------------------------------

def test_urgent_heading_passes_banned_check() -> None:
    """The urgent heading must not contain any banned phrase."""
    problems = _check_banned_phrases(_URGENT_HEADING)
    assert problems == [], f"Urgent heading triggered banned phrase: {problems}"


def test_urgent_body_passes_banned_check() -> None:
    """The urgent body (with placeholders resolved) must not contain any banned phrase."""
    problems = _check_banned_phrases(_URGENT_BODY)
    assert problems == [], f"Urgent body triggered banned phrase: {problems}"


def test_urgent_escalation_prompt_passes_banned_check() -> None:
    """The urgent escalation_prompt (with placeholders resolved) must not contain any banned phrase."""
    problems = _check_banned_phrases(_URGENT_ESCALATION)
    assert problems == [], f"Urgent escalation_prompt triggered banned phrase: {problems}"


# ---------------------------------------------------------------------------
# SCN005–SCN017: urgent answer text must pass final_check
#
# We construct the exact answer text that _build_answer_text() produces for
# a minimal urgent answer: heading + body + escalation_prompt (from messages.json)
# prepended before service lines.  We test the heading/body/escalation_prompt
# against _check_banned_phrases directly, then test a complete answer through
# final_check so that every check is exercised together.
# ---------------------------------------------------------------------------


# SCN005 — Northgate, specialist (SVC014)
def test_scn005_urgent_answer_passes() -> None:
    svc_id = "SVC014"
    answer = _urgent_prefix() + _answer_for(
        svc_id, "Services include: IAPT talking therapies, crisis assessment."
    )
    result = final_check(answer, [svc_id], tier="urgent")
    assert result.passed, f"SCN005 answer failed final_check: {result.problems}"


# SCN006 — Eastfield, hospital_emergency (SVC018)
def test_scn006_urgent_answer_passes() -> None:
    svc_id = "SVC018"
    answer = _urgent_prefix() + _answer_for(svc_id, "Open 24 hours. Call today.")
    result = final_check(answer, [svc_id], tier="urgent")
    assert result.passed, f"SCN006 answer failed final_check: {result.problems}"


# SCN007 — Greenfield, primary_care (SVC002)
def test_scn007_urgent_answer_passes() -> None:
    svc_id = "SVC002"
    answer = _urgent_prefix() + _answer_for(
        svc_id, "GP appointments available. Call today."
    )
    result = final_check(answer, [svc_id], tier="urgent")
    assert result.passed, f"SCN007 answer failed final_check: {result.problems}"


# SCN008 — Riverside, hospital_emergency (SVC008)
def test_scn008_urgent_answer_passes() -> None:
    svc_id = "SVC008"
    answer = _urgent_prefix() + _answer_for(
        svc_id, "Open 24 hours. Emergency care available today."
    )
    result = final_check(answer, [svc_id], tier="urgent")
    assert result.passed, f"SCN008 answer failed final_check: {result.problems}"


# SCN013 — Greenfield, hospital_emergency (SVC003)
def test_scn013_urgent_answer_passes() -> None:
    svc_id = "SVC003"
    answer = _urgent_prefix() + _answer_for(
        svc_id, "Urgent walk-in care available. Attend today."
    )
    result = final_check(answer, [svc_id], tier="urgent")
    assert result.passed, f"SCN013 answer failed final_check: {result.problems}"


# SCN014 — use SVC008 (Riverside A&E, open 24 h) as a safe urgent option
def test_scn014_urgent_answer_passes() -> None:
    svc_id = "SVC008"
    answer = _urgent_prefix() + _answer_for(
        svc_id, "Open 24 hours. Urgent care available today."
    )
    result = final_check(answer, [svc_id], tier="urgent")
    assert result.passed, f"SCN014 answer failed final_check: {result.problems}"


# SCN015 — Westbury, falls back to SVC020 (primary_care)
def test_scn015_urgent_answer_passes() -> None:
    svc_id = "SVC020"
    answer = _urgent_prefix() + _answer_for(
        svc_id, "Same-day GP appointments available. Contact today."
    )
    result = final_check(answer, [svc_id], tier="urgent")
    assert result.passed, f"SCN015 answer failed final_check: {result.problems}"


# SCN016 — Northgate, specialist (SVC014)
def test_scn016_urgent_answer_passes() -> None:
    svc_id = "SVC014"
    answer = _urgent_prefix() + _answer_for(
        svc_id, "Crisis assessment available. Call today."
    )
    result = final_check(answer, [svc_id], tier="urgent")
    assert result.passed, f"SCN016 answer failed final_check: {result.problems}"


# SCN017 — Eastfield, hospital_emergency (SVC018)
def test_scn017_urgent_answer_passes() -> None:
    svc_id = "SVC018"
    answer = _urgent_prefix() + _answer_for(
        svc_id, "Open 24 hours. Acute medicine. Attend today."
    )
    result = final_check(answer, [svc_id], tier="urgent")
    assert result.passed, f"SCN017 answer failed final_check: {result.problems}"


# SCN009–SCN012 (additional urgent scenarios to complete coverage)
def test_scn009_urgent_answer_passes() -> None:
    svc_id = "SVC002"
    answer = _urgent_prefix() + _answer_for(
        svc_id, "Same-day GP appointment. Contact today."
    )
    result = final_check(answer, [svc_id], tier="urgent")
    assert result.passed, f"SCN009 answer failed final_check: {result.problems}"


def test_scn010_urgent_answer_passes() -> None:
    svc_id = "SVC003"
    answer = _urgent_prefix() + _answer_for(
        svc_id, "Urgent walk-in. Attend today."
    )
    result = final_check(answer, [svc_id], tier="urgent")
    assert result.passed, f"SCN010 answer failed final_check: {result.problems}"


def test_scn011_urgent_answer_passes() -> None:
    svc_id = "SVC014"
    answer = _urgent_prefix() + _answer_for(
        svc_id, "Mental health crisis support. Call today."
    )
    result = final_check(answer, [svc_id], tier="urgent")
    assert result.passed, f"SCN011 answer failed final_check: {result.problems}"


def test_scn012_urgent_answer_passes() -> None:
    svc_id = "SVC018"
    answer = _urgent_prefix() + _answer_for(
        svc_id, "24-hour emergency department. Attend today."
    )
    result = final_check(answer, [svc_id], tier="urgent")
    assert result.passed, f"SCN012 answer failed final_check: {result.problems}"
