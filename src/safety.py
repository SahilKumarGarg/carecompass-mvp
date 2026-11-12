"""Safety checker: deterministic red-flag rule matching with no LLM dependency.

Two matching strategies are supported in data/red_flags.json:

1. phrases  — the existing single-substring match (unchanged).
2. term_groups — conjunction of groups: the rule fires when EVERY group has at
   least one synonym present anywhere in the text.  Each synonym check is
   negation-aware: if a negation word appears in a small window before the
   synonym, that synonym is not counted as a match.

Both strategies are independent.  A rule fires if either strategy triggers.

Spelling tolerance (term_groups only):
  For synonyms of 5+ characters, edit distance 1 is also accepted when the
  exact match fails.  At most ONE group per rule invocation may use a fuzzy
  match; the remaining groups must match exactly.  This prevents double-fuzzy
  false positives (e.g. "fevar" + "confusd" each fuzzy-matching separately to
  the same rule still fires, but only because one of the two matches is exact
  after the contraction pre-step and JSON synonym additions).

Performance note:
  The worst-case matching cost scales with the number of tokens in the input.
  src/sanitize.py enforces MAX_INPUT_CHARS = 1_000 before this function is
  called, which bounds the token count and keeps latency predictable.
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

# Minimum synonym length (chars) to attempt a fuzzy (edit-distance-1) match.
_FUZZY_MIN_LEN = 5

# Contraction expansions applied BEFORE punctuation stripping so that both the
# written ("cant") and apostrophe ("can't") forms normalise to the same tokens.
_CONTRACTION_MAP: list[tuple[str, str]] = [
    (r"\bcant\b",    "can't"),
    (r"\bwont\b",    "won't"),
    (r"\bdont\b",    "don't"),
    (r"\bdoesnt\b",  "doesn't"),
    (r"\bdidnt\b",   "didn't"),
    (r"\bwouldnt\b", "wouldn't"),
    (r"\bcouldnt\b", "couldn't"),
    (r"\bisnt\b",    "isn't"),
    (r"\barent\b",   "aren't"),
    (r"\bwasnt\b",   "wasn't"),
    (r"\bwerent\b",  "weren't"),
    (r"\bhasnt\b",   "hasn't"),
    (r"\bhavent\b",  "haven't"),
    (r"\bhadnt\b",   "hadn't"),
]


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
    """Lower-case, expand contractions, strip accents, replace punctuation/whitespace with space."""
    # Step 0: lower-case first so contraction patterns match case-insensitively.
    text = text.lower()
    # Step 1: expand contractions BEFORE removing punctuation so that e.g.
    # "cant" and "can't" both become "can't" and then normalise to "can t".
    for pattern, replacement in _CONTRACTION_MAP:
        text = re.sub(pattern, replacement, text)
    # Step 2: strip Unicode accents.
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    # Step 3: replace all punctuation (including apostrophes) with space.
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Edit-distance helper (Levenshtein, pure Python, no imports)
# ---------------------------------------------------------------------------

def _edit_distance(a: str, b: str) -> int:
    """Return the Levenshtein edit distance between strings *a* and *b*.

    Uses a standard O(len(a) * len(b)) DP matrix.  No external libraries.

    Args:
        a: First string.
        b: Second string.

    Returns:
        Integer edit distance (0 = identical).
    """
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    # dp[i][j] = edit distance between a[:i] and b[:j]
    dp = list(range(lb + 1))
    for i in range(1, la + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, lb + 1):
            temp = dp[j]
            if a[i - 1] == b[j - 1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j - 1])
            prev = temp
    return dp[lb]


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

    Spelling tolerance: after an exact match fails for a group, a fuzzy
    pass (edit distance 1) is attempted for synonyms of _FUZZY_MIN_LEN+ chars.
    Guard against a single misspelt token satisfying multiple groups: each
    fuzzy-matched text token may only be used by one group.

    Args:
        rule: A single red-flag rule dict from red_flags.json.
        text_norm: Normalised full input text.

    Returns:
        "groups:<syn1>+<syn2>+..." on a successful conjunction match, else None.
    """
    groups = rule.get("term_groups", [])
    if not groups:
        return None

    matched_per_group: list[str] = []
    # Fuzzy matching constraints:
    # 1. Only for rules with 2+ groups — a single-group rule (e.g. RF005, RF009)
    #    must match exactly or via phrase; a lone misspelled word must not fire it.
    # 2. At most ONE group per rule may use a fuzzy match — the other group(s) must
    #    match exactly.  This means both groups cannot be misspelled simultaneously
    #    (e.g. "pregant" + "bleading" must NOT fire RF010).
    allow_fuzzy = len(groups) >= 2
    fuzzy_group_used = False          # only one fuzzy group per rule invocation

    for group in groups:
        group_hit: str | None = None

        # --- exact pass (same logic as before) ---
        for synonym in group["synonyms"]:
            syn_norm = _normalise(synonym)
            if syn_norm in text_norm and not _is_negated(syn_norm, text_norm):
                group_hit = synonym
                break

        # --- fuzzy pass (edit distance 1, single-token synonyms only) ---
        if group_hit is None and allow_fuzzy and not fuzzy_group_used:
            text_tokens = text_norm.split()
            for synonym in group["synonyms"]:
                syn_norm = _normalise(synonym)
                # Only attempt fuzzy on single-token synonyms of sufficient length.
                if " " in syn_norm or len(syn_norm) < _FUZZY_MIN_LEN:
                    continue
                for tok in text_tokens:
                    if len(tok) < _FUZZY_MIN_LEN:
                        continue
                    if _edit_distance(tok, syn_norm) == 1:
                        # Check negation: look for the misspelt token in text.
                        if not _is_negated(tok, text_norm):
                            group_hit = synonym + f"~{tok}"  # annotate fuzzy
                            fuzzy_group_used = True
                            break
                if group_hit is not None:
                    break

        if group_hit is None:
            return None   # this group has no match → conjunction fails
        matched_per_group.append(group_hit)

    return "groups:" + "+".join(matched_per_group)


# ---------------------------------------------------------------------------
# Rule cache — loaded once at module import, reset only by tests
# ---------------------------------------------------------------------------

# Pre-populate at module import time so the first call to check_red_flags() pays
# no I/O cost.  Tests that need a fresh load should call clear_cache() first.
_RULES_CACHE: list[dict] | None = None


def _get_rules() -> list[dict]:
    """Return the cached red-flag rule list, loading from disk on first call."""
    global _RULES_CACHE
    if _RULES_CACHE is None:
        with _RED_FLAGS_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
        _RULES_CACHE = data["red_flag_categories"]
    return _RULES_CACHE


def clear_cache() -> None:
    """Reset the in-process rule cache.  Call this only from tests that modify
    the underlying JSON and need the next check_red_flags() call to re-read it.
    Production code must never call this."""
    global _RULES_CACHE
    _RULES_CACHE = None


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
    rules = _get_rules()

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

# Pre-load rules at module import time (never inside a hot path).
_get_rules()
