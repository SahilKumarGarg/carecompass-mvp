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

    who: str                        # e.g. "myself", "my mum", "my 4-year-old"
    concern: str                    # main health concern, in the user's own words
    duration: str                   # how long the problem has been going on
    severity_cues: list[str]        # words/phrases that indicate severity
    area: str                       # the area the user selected
    missing_fields: list[str]       # fields that could not be extracted
    confidence: str                 # "llm" | "offline"
    confidence_score: float         # 0.0–1.0


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
    if result.confidence not in ("llm", "offline"):
        raise ValueError(f"confidence must be 'llm' or 'offline', got {result.confidence!r}")
    if not (0.0 <= result.confidence_score <= 1.0):
        raise ValueError(f"confidence_score must be 0.0–1.0, got {result.confidence_score}")


# ---------------------------------------------------------------------------
# Offline fallback extraction helpers
# ---------------------------------------------------------------------------

_WHO_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bmy (?P<rel>dad|father|mum|mother|mam|brother|sister|son|daughter|"
                r"husband|wife|partner|friend|grandmother|grandfather|gran|grandad|"
                r"child|baby|nephew|niece|uncle|aunt)\b", re.I), "relation"),
    (re.compile(r"\bmy (?P<age>\d+)[- ]year[- ]old\b", re.I), "age"),
    (re.compile(r"\b(?:for |about )?a (?P<age>\d+)[- ]year[- ]old\b", re.I), "age"),
    (re.compile(r"\b(?:i am|i'm|i've|i have|i)\b", re.I), "self"),
]

_DURATION_RE = re.compile(
    r"(?:"
    r"(?:for |since |about |around |over |the (?:past|last) )?"
    r"(?:\d+\s*(?:second|minute|hour|day|week|month|year)s?)"
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

_CONCERN_STOPWORDS = {
    "i", "my", "me", "the", "a", "an", "and", "or", "but", "is", "are",
    "was", "were", "be", "been", "have", "has", "had", "do", "does", "did",
    "he", "she", "they", "we", "it", "his", "her", "their", "our", "its",
    "this", "that", "these", "those", "not", "no", "about", "with", "for",
    "at", "on", "in", "of", "to", "up", "by", "so", "just", "also", "now",
    "very", "quite", "really", "still", "started", "starting", "started",
    "feel", "feeling", "feels", "felt", "think", "thought", "know",
    "ago", "day", "days", "week", "weeks", "hour", "hours", "minute", "minutes",
    "want", "need", "like", "help", "little", "bit",
}


def _extract_who_offline(text: str) -> str:
    """Return a short description of who the case is about."""
    for pattern, kind in _WHO_PATTERNS:
        m = pattern.search(text)
        if m:
            if kind == "self":
                return "myself"
            if kind == "relation":
                return f"my {m.group('rel').lower()}"
            if kind == "age":
                return f"a {m.group('age')}-year-old"
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
    """Heuristic: return the longest non-stopword token sequence as the concern."""
    tokens = re.findall(r"\b[a-z]+\b", text.lower())
    content = [t for t in tokens if t not in _CONCERN_STOPWORDS and len(t) > 2]
    if not content:
        return ""
    # Return at most the first 8 content words joined, to stay readable.
    return " ".join(content[:8])


def _offline_extract(user_text: str, area: str) -> CaseResult:
    """Extract case fields using keyword/regex heuristics only."""
    who = _extract_who_offline(user_text)
    concern = _extract_concern_offline(user_text)
    duration = _extract_duration_offline(user_text)
    severity_cues = _extract_severity_cues_offline(user_text)

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
    )


# ---------------------------------------------------------------------------
# watsonx.ai LLM extraction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a healthcare navigation assistant. "
    "Extract structured information from the user's description. "
    "Respond ONLY with a JSON object with these keys: "
    "who (string), concern (string), duration (string), severity_cues (list of strings). "
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
