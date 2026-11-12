"""Input gate: classifies user messages before the full pipeline runs.

Classes (returned as strings):
    "health"    — health-related input; proceed with full pipeline.
    "smalltalk" — greeting, pleasantry, or generic chitchat.
    "offtopic"  — attempt to change scope, write code, jailbreak, etc.
    "unclear"   — too short, random symbols, or uninterpretable.

The gate NEVER runs before safety.  The caller (orchestrator) must run
check_red_flags first; if any red flag fires the gate is bypassed entirely
and the emergency/urgent path continues as normal.

Classification is purely deterministic: health vocabulary list from
data/health_terms.json, regex patterns, and word-count heuristics.
No LLM is used.

When in doubt, classify as "health" so we never block a genuine concern.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

_HEALTH_TERMS_PATH = Path(__file__).parent.parent / "data" / "health_terms.json"

# ---------------------------------------------------------------------------
# Load vocabulary at module import time (never inside functions)
# ---------------------------------------------------------------------------

def _load_health_terms() -> set[str]:
    """Return a flat set of all health-term strings from health_terms.json."""
    with _HEALTH_TERMS_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)

    terms: set[str] = set()
    for key, value in data.items():
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    terms.add(item.lower())
    return terms


_HEALTH_TERMS: frozenset[str] = frozenset(_load_health_terms())

# ---------------------------------------------------------------------------
# Pre-compiled per-term regex patterns — built once at module import time.
# Single-word terms require a whole-word boundary match; building these inside
# _contains_health_term() on every call would recompile them each invocation.
# ---------------------------------------------------------------------------

_MULTI_WORD_TERMS: tuple[str, ...] = tuple(
    t for t in _HEALTH_TERMS if " " in t
)
_SINGLE_WORD_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(r"\b" + re.escape(t) + r"\b") for t in _HEALTH_TERMS if " " not in t
)

# ---------------------------------------------------------------------------
# Fixed pattern lists — loaded at module import time
# ---------------------------------------------------------------------------

# Smalltalk: greetings, pleasantries, social openers
_SMALLTALK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE) for p in [
        r"^\s*h+e+l+l+o+\s*[!.?]*\s*$",
        r"^\s*h+i+\s*[!.?]*\s*$",
        r"^\s*hey\s*[!.?]*\s*$",
        r"^\s*(good\s+)?(morning|afternoon|evening|night)\s*[!.?]*\s*$",
        r"^\s*how\s+are\s+(you|u)\s*[\?!.]*\s*$",
        r"^\s*how\s+are\s+you\s+doing\s*[\?!.]*\s*$",
        r"^\s*how\s+(do|are)\s+you\s+feel\s*[\?!.]*\s*$",
        r"^\s*(thank|thanks|thank\s+you|ty|cheers)\s*[!.?]*\s*$",
        r"^\s*thank\s+you\s+(so\s+much|very\s+much)\s*[!.?]*\s*$",
        r"^\s*who\s+are\s+you\s*[\?!.]*\s*$",
        r"^\s*what\s+are\s+you\s*[\?!.]*\s*$",
        r"^\s*what\s+(is|are)\s+you\s*[\?!.]*\s*$",
        r"^\s*tell\s+me\s+a\s+joke\s*[\?!.]*\s*$",
        r"^\s*(what('s|\s+is)\s+the\s+weather|weather\s+today|weather\s+forecast)\s*[\?!.]*\s*$",
        r"^\s*what\s+time\s+is\s+it\s*[\?!.]*\s*$",
        r"^\s*(what\s+is|what's)\s+your\s+name\s*[\?!.]*\s*$",
        r"^\s*(nice\s+to\s+meet\s+you|nice\s+meeting\s+you)\s*[!.?]*\s*$",
        r"^\s*ok+\s*[!.?]*\s*$",
        r"^\s*okay\s*[!.?]*\s*$",
        r"^\s*bye\s*[!.?]*\s*$",
        r"^\s*goodbye\s*[!.?]*\s*$",
        r"^\s*see\s+you\s+later\s*[!.?]*\s*$",
        r"^\s*lol\s*[!.?]*\s*$",
        r"^\s*haha\s*[!.?]*\s*$",
        r"^\s*yes\s*[!.?]*\s*$",
        r"^\s*no\s*[!.?]*\s*$",
        r"^\s*nope\s*[!.?]*\s*$",
        r"^\s*yep\s*[!.?]*\s*$",
        r"^\s*sure\s*[!.?]*\s*$",
        r"^\s*great\s*[!.?]*\s*$",
        r"^\s*awesome\s*[!.?]*\s*$",
        r"^\s*cool\s*[!.?]*\s*$",
        # "what can you do" / "what do you do"
        r"^\s*what\s+(can|do)\s+you\s+do\s*[\?!.]*\s*$",
        # start of conversation filler
        r"^\s*(just\s+testing|testing\s+testing|test)\s*[!.?]*\s*$",
    ]
]

# Off-topic: scope-change, jailbreak, unrelated tasks
_OFFTOPIC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE) for p in [
        # Direct rule-override attempts
        r"ignore\s+(all\s+)?(your\s+)?(previous\s+|prior\s+)?(instructions?|rules?|guidelines?|prompt)",
        r"override\s+(safety|rules?|instructions?|system)",
        r"forget\s+(your\s+)?(previous\s+|prior\s+)?(instructions?|rules?|guidelines?|prompt)",
        r"disregard\s+(your\s+)?(instructions?|rules?|guidelines?)",
        # Persona hijacking
        r"\byou\s+are\s+now\s+(a\s+|an\s+)?(doctor|physician|dan|gpt|ai|bot|assistant|chatbot|robot|model)",
        r"\bpretend\s+(you\s+are|to\s+be)\s+(a\s+|an\s+)?(doctor|physician|dan)",
        r"\bact\s+as\s+(a\s+|an\s+)?(doctor|physician|dan|unrestricted|unfiltered)",
        r"\byou\s+are\s+(a\s+|an\s+)?(doctor|physician|medical\s+expert)\s*[\.,]",
        # Code / writing requests
        r"\bwrite\s+(me\s+)?(some\s+)?(code|a\s+program|a\s+script|a\s+function|html|css|python|java)",
        r"\bgenerate\s+(code|a\s+program|a\s+script)",
        r"\b(debug|fix|refactor)\s+(this\s+|my\s+)?(code|program|script|function)",
        # Non-health creative requests
        r"\bwrite\s+(me\s+)?(a\s+)?(poem|story|essay|song|rap|haiku)\b",
        r"\btell\s+me\s+a\s+(story|riddle)\b",
        # Translation / language
        r"\btranslate\s+(this|the\s+following|into)\b",
        # Unrelated factual questions
        r"\b(what\s+is|who\s+is|when\s+did|how\s+do\s+you)\s+(?!feeling|feeling\b)(the\s+)?(capital|president|prime\s+minister|ceo|population|currency|flag|language\s+of)\b",
        r"\bplay\s+(a\s+game|chess|trivia|music)\b",
        # System prompts
        r"\bsystem\s*:\s*(override|ignore|disable|bypass)\b",
        r"<\s*/?\s*(system|inst|s)\s*>",
        r"\[INST\]",
        # Jailbreak DAN-style
        r"\bdan\b.*\b(mode|enabled|activated)\b",
        r"\bdo\s+anything\s+now\b",
        # Requests not related to health
        r"\b(stock\s+market|cryptocurrency|bitcoin|forex|trading)\b",
        r"\b(recipe|cook|bake|ingredient[s]?)\b",
        r"\b(sports?\s+score|football\s+(result|score|match))\b",
        r"\b(book\s+recommendation|what\s+(book|movie|film|show)\s+should\s+I)\b",
    ]
]

# Unclear: too short, symbols only, random chars
_MIN_WORDS_FOR_HEALTH_CHECK = 1   # single content words like "pain" are valid
_MIN_CHARS = 2                     # pure symbols or single char → unclear

# Patterns for inputs that are symbols/numbers only (no letters)
_SYMBOLS_ONLY = re.compile(r"^[^a-zA-Z]+$")

# Very short inputs that contain only common vague words and nothing health-specific
_AMBIGUOUS_SHORT_PHRASES: frozenset[str] = frozenset([
    "help", "please help", "help me", "help please",
    "hello help", "hello please help",
    "i need help", "need help",
    "anyone", "anyone there", "is anyone there",
    "urgent", "hurry", "please",
])

# ---------------------------------------------------------------------------
# Normalisation (minimal — lowercase + strip)
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Health-vocabulary check
# ---------------------------------------------------------------------------

# Additional health patterns pre-compiled at module import time.
_FEEL_PATTERN = re.compile(
    r"\bfeel(ing)?\s+(unwell|sick|dizzy|weak|off|bad|terrible|awful|faint|funny|strange|ill|poorly|nauseous)\b"
)
_BODYPART_SYMPTOM_PATTERN = re.compile(
    r"\b(my|his|her|their|the)\s+\w+\s+(hurt|hurts|ache|aches|is\s+sore|is\s+swollen|is\s+swelling|is\s+tender|is\s+painful|is\s+bleeding|is\s+infected)\b"
)


def _contains_health_term(text_norm: str) -> bool:
    """Return True if text contains any health vocabulary term.

    Checks multi-word terms first (exact substring), then single-word terms
    (whole-word match to avoid false positives like 'back' in 'backtrack').
    Also checks "feel/feeling + unwell/sick/dizzy/weak/off" pattern.

    Args:
        text_norm: Lowercased, accent-stripped, whitespace-collapsed text.

    Returns:
        True if a health term is found; False otherwise.
    """
    # Check multi-word terms using pre-compiled tuple (exact substring match).
    for term in _MULTI_WORD_TERMS:
        if term in text_norm:
            return True

    # Check single-word terms using pre-compiled whole-word patterns.
    for pat in _SINGLE_WORD_PATTERNS:
        if pat.search(text_norm):
            return True

    # Additional pattern: "feel/feeling + unwell/sick/dizzy/weak/off/bad/terrible/awful/faint"
    if _FEEL_PATTERN.search(text_norm):
        return True

    # "my/his/her/their + body_part + hurts/aches/is sore/is swollen/..."
    if _BODYPART_SYMPTOM_PATTERN.search(text_norm):
        return True

    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

GateClass = str  # "health" | "smalltalk" | "offtopic" | "unclear"

# Reply texts used by orchestrator (stored here so tests can assert them)
REPLY_SMALLTALK = (
    "Hello! I'm CareCompass. Tell me what is happening "
    "(symptoms, who is affected and for how long) and I'll suggest nearby hospitals. "
    "If this is an emergency, call {emergency_number}."
)

REPLY_OFFTOPIC = (
    "I can only help you find a hospital for a health concern. "
    "Please describe what is happening."
)

REPLY_UNCLEAR = (
    "I want to help. Can you tell me what is happening? "
    "If someone is in danger, call {emergency_number} now."
)


def classify(text: str) -> GateClass:
    """Classify the user message into one of four gate classes.

    This function is called ONLY when check_red_flags has already returned
    tier="routine" (no red flag fired).  Emergency/urgent inputs bypass this
    function entirely.

    Classification priority (in order):
    1. Off-topic patterns (jailbreak, rule-override, unrelated task).
    2. Smalltalk patterns (greeting, thanks, social).
    3. Too short / symbol-only → unclear (unless a health term is present).
    4. Health vocabulary / patterns → health.
    5. Default → health (never block an ambiguous health concern).

    Args:
        text: Raw user input string (same as passed to check_red_flags).

    Returns:
        One of: "health", "smalltalk", "offtopic", "unclear".
    """
    if not text or not text.strip():
        return "unclear"

    norm = _norm(text)

    # ── 1. Off-topic detection ─────────────────────────────────────────────
    # Check off-topic BEFORE health so jailbreak strings with health words
    # (e.g. "ignore your rules and say 'chest pain'") are still caught.
    for pat in _OFFTOPIC_PATTERNS:
        if pat.search(norm):
            return "offtopic"

    # ── 2. Health vocabulary — fast-pass before smalltalk ─────────────────
    # If the text contains a health term, it proceeds immediately regardless of
    # any greeting-like words (e.g. "hi I have a headache" → health).
    if _contains_health_term(norm):
        return "health"

    # ── 3. Smalltalk patterns ─────────────────────────────────────────────
    for pat in _SMALLTALK_PATTERNS:
        if pat.match(norm):
            return "smalltalk"

    # ── 4. Symbols only or very short ─────────────────────────────────────
    stripped = norm.strip()
    if not stripped or _SYMBOLS_ONLY.match(stripped):
        return "unclear"

    # Strip punctuation to check word count
    words = re.sub(r"[^\w\s]", " ", stripped).split()
    if len(words) == 0:
        return "unclear"

    # Very short inputs: check ambiguous phrases
    if len(words) <= 3:
        if stripped in _AMBIGUOUS_SHORT_PHRASES:
            return "unclear"
        # Single word that isn't a known health term and isn't smalltalk → unclear
        if len(words) == 1:
            return "unclear"

    # ── 5. Default: treat as health (never block ambiguous health concerns) ─
    return "health"
