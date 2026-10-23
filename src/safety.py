"""Safety checker: deterministic red-flag rule matching with no LLM dependency."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import NamedTuple

# Tier ordering: higher index = higher severity (highest tier wins).
_TIER_ORDER = {"routine": 0, "urgent": 1, "emergency": 2}

_RED_FLAGS_PATH = Path(__file__).parent.parent / "data" / "red_flags.json"


class RuleHit(NamedTuple):
    """A single red-flag rule that matched the input text."""

    rule_id: str
    name: str
    tier: str
    matched_phrase: str


class SafetyResult(NamedTuple):
    """Result of a red-flag check."""

    tier: str          # "emergency" | "urgent" | "routine"
    hits: list[RuleHit]  # all matching rules, sorted highest tier first


def _normalise(text: str) -> str:
    """Lower-case, strip accents, replace punctuation/whitespace runs with a space."""
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^\w\s]", " ", text)   # replace all punctuation with space
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _load_rules() -> list[dict]:
    """Load red-flag categories from data/red_flags.json."""
    with _RED_FLAGS_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return data["red_flag_categories"]


def check_red_flags(text: str) -> SafetyResult:
    """Check *text* against all red-flag rules.

    Returns a SafetyResult with the highest-severity tier found and the list
    of every rule that matched.  Returns tier "routine" with an empty hits list
    when no rules match.  No LLM or network calls are made.

    Args:
        text: Raw user input string.

    Returns:
        SafetyResult(tier, hits) where tier is the highest tier across all hits.
    """
    normalised = _normalise(text)
    rules = _load_rules()

    hits: list[RuleHit] = []
    for rule in rules:
        for phrase in rule["phrases"]:
            norm_phrase = _normalise(phrase)
            if norm_phrase in normalised:
                hits.append(
                    RuleHit(
                        rule_id=rule["id"],
                        name=rule["name"],
                        tier=rule["tier"],
                        matched_phrase=phrase,
                    )
                )
                break  # one hit per rule category is enough

    if not hits:
        return SafetyResult(tier="routine", hits=[])

    # Highest tier wins.
    hits.sort(key=lambda h: _TIER_ORDER[h.tier], reverse=True)
    top_tier = hits[0].tier
    return SafetyResult(tier=top_tier, hits=hits)
