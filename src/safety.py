"""Safety checker: deterministic red-flag rule matching with no LLM dependency.

Two matching strategies are supported in data/red_flags.json:

1. phrases  — the existing single-substring match (unchanged).
2. term_groups — conjunction of groups: the rule fires when EVERY group has at
   least one synonym present anywhere in the text.  Each synonym check is
   negation-aware: if a negation word appears in a small window before the
   synonym, that synonym is not counted as a match.

Both strategies are independent.  A rule fires if either strategy triggers.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import NamedTuple

# Tier ordering: higher index = higher severity (highest tier wins).
_TIER_ORDER = {"routine": 0, "urgent": 1, "emergency": 2}

_RED_FLAGS_PATH = Path(__file__).parent.parent / "data" / "red_flags.json"

# Words that negate the following term when they appear in a small look-back window.
_NEGATORS = {"no", "not", "without", "never", "dont", "doesnt", "didnt", "neither", "nor"}

# How many tokens before the synonym to search for a negator.
_NEGATION_WINDOW = 3


# ---------------------------------------------------------------------------
# Named-tuple output types (unchanged public API)
# ---------------------------------------------------------------------------

class RuleHit(NamedTuple):
    """A single red-flag rule that matched the input text."""

    rule_id: str
    name: str
    tier: str
    matched_phrase: str   # the phrase or synonym that triggered the match


class SafetyResult(NamedTuple):
    """Result of a red-flag check."""

    tier: str            # "emergency" | "urgent" | "routine"
    hits: list[RuleHit]  # all matching rules, sorted highest tier first


# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """Lower-case, strip accents, replace punctuation/whitespace runs with a space."""
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^\w\s]", " ", text)   # replace all punctuation with space
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Negation helper
# ---------------------------------------------------------------------------

def _is_negated(synonym_norm: str, text_norm: str) -> bool:
    """Return True if *synonym_norm* is immediately preceded by a negation word.

    Splits text into tokens and, at each occurrence of the synonym (which may
    span multiple tokens), looks at up to _NEGATION_WINDOW tokens before the
    match for a negator.

    Args:
        synonym_norm: Normalised synonym string (may be multi-word).
        text_norm: Normalised full input text.

    Returns:
        True if every occurrence of the synonym in the text is negated.
        False if at least one occurrence is NOT negated (rule should fire).
    """
    if synonym_norm not in text_norm:
        return False   # not present at all — not "negated", just absent

    tokens = text_norm.split()
    syn_tokens = synonym_norm.split()
    syn_len = len(syn_tokens)

    any_non_negated = False
    i = 0
    while i <= len(tokens) - syn_len:
        if tokens[i : i + syn_len] == syn_tokens:
            # Found an occurrence.  Check preceding window for negators.
            window_start = max(0, i - _NEGATION_WINDOW)
            window = tokens[window_start:i]
            if not any(w in _NEGATORS for w in window):
                any_non_negated = True
                break
        i += 1

    return not any_non_negated


# ---------------------------------------------------------------------------
# Matching strategies
# ---------------------------------------------------------------------------

def _match_phrases(rule: dict, text_norm: str) -> str | None:
    """Return the first matching phrase or None.

    Unchanged from the original implementation: a phrase matches when its
    normalised form is a substring of the normalised text.
    """
    for phrase in rule.get("phrases", []):
        if _normalise(phrase) in text_norm:
            return phrase
    return None


def _match_term_groups(rule: dict, text_norm: str) -> str | None:
    """Return a description of what matched, or None.

    All groups must each have at least one synonym present and not negated.
    Returns a summary string (e.g. "groups:fever+confused") on success.
    """
    groups = rule.get("term_groups", [])
    if not groups:
        return None

    matched_per_group: list[str] = []
    for group in groups:
        group_hit: str | None = None
        for synonym in group["synonyms"]:
            syn_norm = _normalise(synonym)
            if syn_norm in text_norm and not _is_negated(syn_norm, text_norm):
                group_hit = synonym
                break
        if group_hit is None:
            return None   # this group has no match → conjunction fails
        matched_per_group.append(group_hit)

    return "groups:" + "+".join(matched_per_group)


# ---------------------------------------------------------------------------
# Data loader
# ---------------------------------------------------------------------------

def _load_rules() -> list[dict]:
    """Load red-flag categories from data/red_flags.json."""
    with _RED_FLAGS_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return data["red_flag_categories"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_red_flags(text: str) -> SafetyResult:
    """Check *text* against all red-flag rules.

    Each rule is tried with both the phrases strategy and the term_groups
    strategy; the rule fires if either produces a match.  Returns a
    SafetyResult with the highest-severity tier found across all matching
    rules.  Returns tier "routine" with an empty hits list when no rules
    match.  No LLM or network calls are made.

    Args:
        text: Raw user input string.

    Returns:
        SafetyResult(tier, hits) where tier is the highest tier across all hits.
    """
    text_norm = _normalise(text)
    rules = _load_rules()

    hits: list[RuleHit] = []
    for rule in rules:
        matched_phrase = _match_phrases(rule, text_norm)
        if matched_phrase is None:
            matched_phrase = _match_term_groups(rule, text_norm)
        if matched_phrase is not None:
            hits.append(
                RuleHit(
                    rule_id=rule["id"],
                    name=rule["name"],
                    tier=rule["tier"],
                    matched_phrase=matched_phrase,
                )
            )

    if not hits:
        return SafetyResult(tier="routine", hits=[])

    # Highest tier wins.
    hits.sort(key=lambda h: _TIER_ORDER[h.tier], reverse=True)
    top_tier = hits[0].tier
    return SafetyResult(tier=top_tier, hits=hits)
