"""Intake module: extract a structured case from free-form user text.

Uses watsonx.ai when credentials are available; falls back to offline
keyword/regex extraction otherwise.  No LLM call is ever required.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
from typing import Optional


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class CaseResult:
    """Structured representation of the user's situation."""

    who: str                        # e.g. "myself", "my mum", "my 6-year-old daughter"
    concern: str                    # main health concern, in the user's own words
    duration: str                   # how long the problem has been going on
    severity_cues: list[str]        # words/phrases that indicate severity
    area: str                       # the area the user selected
    missing_fields: list[str]       # fields that could not be extracted
    confidence: str                 # "llm" | "offline"
    confidence_score: float         # 0.0–1.0
    denied_symptoms: list[str]      # negated symptoms e.g. ["no fever", "not in pain"]


def _validate(result: CaseResult) -> None:
    """Raise ValueError if result violates the schema."""
    if not isinstance(result.who, str):
        raise ValueError("who must be a str")
    if not isinstance(result.concern, str):
        raise ValueError("concern must be a str")
    if not isinstance(result.duration, str):
        raise ValueError("duration must be a str")
    if not isinstance(result.severity_cues, list):
        raise ValueError("severity_cues must be a list")
    if not isinstance(result.denied_symptoms, list):
        raise ValueError("denied_symptoms must be a list")
    if result.confidence not in ("llm", "offline"):
        raise ValueError(f"confidence must be 'llm' or 'offline', got {result.confidence!r}")
    if not (0.0 <= result.confidence_score <= 1.0):
        raise ValueError(f"confidence_score must be 0.0–1.0, got {result.confidence_score}")


# ---------------------------------------------------------------------------
# Offline fallback extraction helpers
# ---------------------------------------------------------------------------

# Relation words used in who-patterns and for stripping from concern.
_RELATION_WORDS = (
    "dad|father|mum|mother|mam|brother|sister|son|daughter|"
    "husband|wife|partner|friend|grandmother|grandfather|gran|grandad|"
    "child|baby|nephew|niece|uncle|aunt"
)

# Matches "my 6-year-old daughter", "my 4-year-old", "a 6-year-old"
# capturing optional relation after the age.
_WHO_AGE_RE = re.compile(
    r"\bmy (?P<age>\d+)[- ]year[- ]old"
    r"(?:\s+(?P<rel>" + _RELATION_WORDS + r"))?",
    re.I,
)
_WHO_AGE_BARE_RE = re.compile(
    r"\b(?:for |about )?a (?P<age>\d+)[- ]year[- ]old\b",
    re.I,
)

_WHO_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(
        r"\bmy (?P<rel>" + _RELATION_WORDS + r")\b",
        re.I,
    ), "relation"),
    (_WHO_AGE_RE, "age_mine"),
    (_WHO_AGE_BARE_RE, "age_bare"),
    (re.compile(r"\b(?:i am|i'm|i've|i have|i)\b", re.I), "self"),
]

# Word-spelled numbers for duration extraction.
_WORD_NUMS = {
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "couple": "2", "few": "3",
}

_DURATION_RE = re.compile(
    r"(?:"
    # digit-led: "for 3 days", "about 10 minutes ago", "the past 2 weeks"
    r"(?:for |since |about |around |over |the (?:past|last) )?"
    r"(?:\d+\s*(?:second|minute|hour|day|week|month|year)s?)"
    # word-number led: "three days", "for two weeks", "couple of days"
    r"|(?:for |about |around |over |the (?:past|last) )?"
    r"(?:" + "|".join(_WORD_NUMS) + r")"
    r"(?:\s+of)?\s+(?:second|minute|hour|day|week|month|year)s?"
    # relative anchors
    r"|since (?:yesterday|this morning|last night|last week|last month)"
    r"|(?:a|an) (?:few (?:minutes|hours|days|weeks)|couple of (?:days|weeks))"
    r"|just now|suddenly|earlier today|this morning|this afternoon|this evening"
    r")",
    re.I,
)

_SEVERITY_WORDS = [
    "severe", "very severe", "extreme", "excruciating", "unbearable",
    "mild", "slight", "minor", "moderate",
    "suddenly", "suddenly started", "came on suddenly", "came on really suddenly",
    "getting worse", "worsening", "rapidly", "quickly",
    "can't", "cannot", "unable to",
    "a lot", "a lot of",
    "very bad", "really bad", "really severe",
    "high temperature", "high fever", "very high",
    "heavily", "heavy",
]

# Negated-symptom extraction: "no fever", "not feverish", "without pain", etc.
# The second word (optional) must not be a conjunction or common filler.
_NEGATED_SYMPTOM_RE = re.compile(
    r"(?:"
    r"(?:no|not|without|never)\s+(?:any\s+)?"
    r"(?:[a-z]+(?:\s+(?!and\b|but\b|or\b|so\b|the\b|a\b|an\b)[a-z]+)?)"
    r")",
    re.I,
)

# Filler words that appear in negated matches but aren't symptoms on their own.
_NEGATION_FILLER = {
    "no", "not", "without", "never", "any",
    "i", "my", "the", "a", "an",
}

# Symptom-like words — a match is kept only if it contains at least one of these.
_SYMPTOM_WORDS = {
    "fever", "temperature", "pain", "cough", "vomit", "nausea", "rash",
    "bleed", "blood", "swelling", "breathless", "breath", "dizzy", "dizziness",
    "headache", "feverish", "sick", "ache", "aching", "sore", "weakness",
    "confusion", "confused", "fit", "seizure", "unconscious", "faint",
    "appetite", "eating", "sleeping", "sleep",
}


def _extract_who_offline(text: str) -> str:
    """Return a short description of who the case is about."""
    # Check age patterns first — "my 6-year-old daughter" is more specific
    # than matching "my daughter" alone.
    m = _WHO_AGE_RE.search(text)
    if m:
        age = m.group("age")
        rel = m.group("rel")
        if rel:
            return f"my {age}-year-old {rel.lower()}"
        return f"my {age}-year-old"

    m = _WHO_AGE_BARE_RE.search(text)
    if m:
        return f"a {m.group('age')}-year-old"

    # Fall through: check relation and self patterns.
    # _WHO_PATTERNS[0] = relation, _WHO_PATTERNS[3] = self  (indices 1,2 are age, already done)
    for idx in (0, 3):
        pattern, kind = _WHO_PATTERNS[idx]
        m = pattern.search(text)
        if m:
            if kind == "self":
                return "myself"
            if kind == "relation":
                return f"my {m.group('rel').lower()}"
    return ""


def _extract_duration_offline(text: str) -> str:
    """Return the first duration expression found, or empty string."""
    m = _DURATION_RE.search(text)
    return m.group(0).strip() if m else ""


def _extract_severity_cues_offline(text: str) -> list[str]:
    """Return severity words/phrases found in text (case-insensitive)."""
    lower = text.lower()
    return [w for w in _SEVERITY_WORDS if w in lower]


def _extract_concern_offline(text: str) -> str:
    """Return the user's own words as the concern.

    Strategy: strip the who-description and duration from the original text,
    then return what remains (up to ~120 chars), trimmed of leading connectives.
    Falls back to the full text trimmed if stripping leaves nothing.
    """
    working = text.strip()

    # Remove who phrase (e.g. "My 6-year-old daughter", "My mum").
    working = re.sub(
        r"(?:my\s+(?:\d+[- ]year[- ]old\s+)?(?:" + _RELATION_WORDS + r")|"
        r"my\s+\d+[- ]year[- ]old|"
        r"(?:for |about )?a \d+[- ]year[- ]old)\b",
        "",
        working,
        flags=re.I,
    )

    # Remove leading connective words left by the strip.
    working = re.sub(r"^[\s,;.]*(?:has|have|is|was|had|and|but|who|with)\b[\s,;.]*", "", working, flags=re.I)

    # Remove duration phrase.
    working = _DURATION_RE.sub("", working)

    # Clean up punctuation runs and extra whitespace.
    working = re.sub(r"\s{2,}", " ", working).strip(" .,;")

    # Return up to 120 chars of what's left, else fall back to full text.
    result = working[:120].strip(" .,;")
    return result if result else text[:120].strip()


def _extract_denied_symptoms_offline(text: str) -> list[str]:
    """Return a list of negated symptom phrases (e.g. 'no fever', 'not in pain').

    Only phrases that contain a known symptom word are returned, so generic
    negations like "no problem" are not included.
    """
    found: list[str] = []
    for m in _NEGATED_SYMPTOM_RE.finditer(text):
        phrase = m.group(0).strip().lower()
        # Keep only if the phrase contains a symptom word.
        words = set(phrase.split())
        if words & _SYMPTOM_WORDS:
            found.append(phrase)
    return found


def _clean_denied_from_concern(concern: str, denied: list[str]) -> str:
    """Remove denied-symptom phrases from the concern string."""
    result = concern
    for phrase in denied:
        result = re.sub(re.escape(phrase), "", result, flags=re.I)
    # Strip leftover connective words that appear at start/end or doubled-up.
    result = re.sub(r"\b(?:and|but|or)\b", " ", result, flags=re.I)
    result = re.sub(r"\s{2,}", " ", result)
    result = result.strip(" .,;")
    return result


def _offline_extract(user_text: str, area: str) -> CaseResult:
    """Extract case fields using keyword/regex heuristics only."""
    who = _extract_who_offline(user_text)
    duration = _extract_duration_offline(user_text)
    severity_cues = _extract_severity_cues_offline(user_text)
    denied_symptoms = _extract_denied_symptoms_offline(user_text)
    concern = _clean_denied_from_concern(
        _extract_concern_offline(user_text), denied_symptoms
    )

    missing: list[str] = []
    filled = 0
    for field, val in (("who", who), ("concern", concern), ("duration", duration)):
        if not val:
            missing.append(field)
        else:
            filled += 1

    score = round(filled / 3, 2)
    return CaseResult(
        who=who,
        concern=concern,
        duration=duration,
        severity_cues=severity_cues,
        area=area,
        missing_fields=missing,
        confidence="offline",
        confidence_score=score,
        denied_symptoms=denied_symptoms,
    )


# ---------------------------------------------------------------------------
# watsonx.ai LLM extraction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a healthcare navigation assistant. "
    "Extract structured information from the user's description. "
    "Respond ONLY with a JSON object with these keys: "
    "who (string), concern (string), duration (string), "
    "severity_cues (list of strings), denied_symptoms (list of strings). "
    "denied_symptoms should contain negated symptom phrases such as 'no fever'. "
    "Do not diagnose. Do not suggest medicines or treatments. "
    "If a field cannot be determined, use an empty string or empty list."
)


def _llm_extract(user_text: str, area: str) -> CaseResult:
    """Extract case fields using watsonx.ai.  Falls back to offline on any error."""
    try:
        from ibm_watsonx_ai import APIClient, Credentials  # type: ignore
        from ibm_watsonx_ai.foundation_models import ModelInference  # type: ignore

        api_key = os.environ["WATSONX_API_KEY"]
        url = os.environ["WATSONX_URL"]
        project_id = os.environ["WATSONX_PROJECT_ID"]

        credentials = Credentials(url=url, api_key=api_key)
        client = APIClient(credentials)
        model = ModelInference(
            model_id="ibm/granite-13b-instruct-v2",
            api_client=client,
            project_id=project_id,
        )

        prompt = f"{_SYSTEM_PROMPT}\n\nUser description: {user_text}\n\nJSON:"
        response = model.generate_text(prompt=prompt, guardrails=False)
        raw = response.strip()

        # Extract the first {...} block from the response.
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            raise ValueError("No JSON object in LLM response")
        parsed = json.loads(m.group(0))

        who = str(parsed.get("who", "")).strip()
        concern = str(parsed.get("concern", "")).strip()
        duration = str(parsed.get("duration", "")).strip()
        severity_cues_raw = parsed.get("severity_cues", [])
        severity_cues = [str(c) for c in severity_cues_raw] if isinstance(severity_cues_raw, list) else []
        denied_raw = parsed.get("denied_symptoms", [])
        denied_symptoms = [str(d) for d in denied_raw] if isinstance(denied_raw, list) else []

        missing: list[str] = []
        for field, val in (("who", who), ("concern", concern), ("duration", duration)):
            if not val:
                missing.append(field)

        filled = 3 - len(missing)
        score = round(0.6 + (filled / 3) * 0.4, 2)  # LLM baseline is higher

        result = CaseResult(
            who=who,
            concern=concern,
            duration=duration,
            severity_cues=severity_cues,
            area=area,
            missing_fields=missing,
            confidence="llm",
            confidence_score=score,
            denied_symptoms=denied_symptoms,
        )
        _validate(result)
        return result

    except Exception:  # noqa: BLE001
        # Any failure (import, network, parse) falls back to offline.
        return _offline_extract(user_text, area)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _watsonx_available() -> bool:
    """Return True when all three watsonx.ai env vars are set and non-empty."""
    return all(
        os.environ.get(k, "").strip()
        for k in ("WATSONX_API_KEY", "WATSONX_URL", "WATSONX_PROJECT_ID")
    )


def extract_case(user_text: str, area: str) -> CaseResult:
    """Extract a structured case from *user_text* for the given *area*.

    Uses watsonx.ai when credentials are present, offline keyword/regex
    extraction otherwise.  Always validates the result before returning.

    Args:
        user_text: Raw description from the user.
        area: The area the user selected (passed through unchanged).

    Returns:
        CaseResult with extracted fields, missing_fields list, and confidence.
    """
    if _watsonx_available():
        result = _llm_extract(user_text, area)
    else:
        result = _offline_extract(user_text, area)
    _validate(result)
    return result


def get_followup_question(missing_fields: list[str], questions_asked: int) -> Optional[str]:
    """Return the next follow-up question to ask, or None if limit reached.

    At most 2 follow-up questions are ever generated.

    Args:
        missing_fields: Fields that could not be extracted (e.g. ["who", "duration"]).
        questions_asked: How many follow-up questions have already been asked this session.

    Returns:
        A question string, or None when questions_asked >= 2 or nothing is missing.
    """
    if questions_asked >= 2 or not missing_fields:
        return None

    _questions: dict[str, str] = {
        "who": "Just to help me find the right service — is this for yourself or someone else?",
        "duration": "How long has this been going on?",
        "concern": "Could you tell me a little more about what's worrying you?",
    }
    for field in ("who", "concern", "duration"):
        if field in missing_fields:
            return _questions[field]
    return None
