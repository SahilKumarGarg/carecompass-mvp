"""Input sanitisation for CareCompass.

All user-supplied strings must pass through ``sanitize_user_input`` before any
regex matching, LLM call, or display.  This enforces:

1. Reject None / non-string / binary (contains NUL bytes after encoding).
2. Unicode NFKC normalisation.
3. Strip control characters (categories Cc, Cf) including zero-width joiners,
   soft hyphens, etc.; strip bidirectional override characters explicitly.
4. Collapse runs of whitespace (\\t, \\r, \\n, multiple spaces) to a single space.
5. Hard cap at MAX_INPUT_CHARS characters; returns a truncated copy and sets the
   ``was_truncated`` flag so the caller can inform the user.
6. Reject the result if it is empty or consists only of whitespace after the
   above steps.

The function is deliberately cheap (no regex that can catastrophically
backtrack, no network, no LLM) so it can run before anything else.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

# Maximum number of Unicode code-points accepted.  Applied BEFORE any regex so
# very long strings cannot cause slow matching.
MAX_INPUT_CHARS = 1_000

# Bidirectional override code points (U+202A–U+202E, U+2066–U+2069, U+200F RLM,
# U+200E LRM, U+061C ALM) that are used in "Trojan Source" style attacks.
_BIDI_OVERRIDES: frozenset[str] = frozenset(
    "\u200e\u200f\u061c"
    "\u202a\u202b\u202c\u202d\u202e"
    "\u2066\u2067\u2068\u2069"
)

# Characters considered "binary": NUL byte.  We detect these in the *raw*
# string (before any normalisation).
_NUL = "\x00"


@dataclass(frozen=True)
class SanitizeResult:
    """Outcome of a ``sanitize_user_input`` call."""

    text: str           # The cleaned text (empty string on rejection).
    ok: bool            # True when the text is safe to use.
    was_truncated: bool # True when the original was longer than MAX_INPUT_CHARS.
    error: str          # Human-readable rejection reason (empty when ok=True).


def sanitize_user_input(raw: Any) -> SanitizeResult:
    """Sanitise a user-supplied input string.

    Args:
        raw: The value received from the UI (expected to be str).

    Returns:
        SanitizeResult describing the outcome.  Always check ``.ok`` before
        using ``.text``.
    """
    # ── 1. Type / binary guard ─────────────────────────────────────────────
    if not isinstance(raw, str):
        return SanitizeResult(text="", ok=False, was_truncated=False,
                              error="Input must be a text string.")
    if _NUL in raw:
        return SanitizeResult(text="", ok=False, was_truncated=False,
                              error="Input contains binary data.")

    # ── 2. Unicode NFKC normalisation ──────────────────────────────────────
    text: str = unicodedata.normalize("NFKC", raw)

    # ── 3. Strip bidi overrides and control characters ─────────────────────
    # Bidi overrides first (explicit set), then Unicode control/format chars.
    # We keep whitespace-class characters (category Zs) and plain spaces.
    cleaned: list[str] = []
    for ch in text:
        if ch in _BIDI_OVERRIDES:
            continue  # drop silently
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf"):
            # Keep ordinary whitespace (0x09 tab, 0x0A LF, 0x0D CR) so we can
            # collapse them in step 4; drop everything else in Cc/Cf.
            if ch in ("\t", "\n", "\r"):
                cleaned.append(" ")  # treat as space for collapse step
            # else: drop (zero-width space, zero-width joiner, etc.)
        else:
            cleaned.append(ch)
    text = "".join(cleaned)

    # ── 4. Collapse whitespace ─────────────────────────────────────────────
    # All internal runs of whitespace → single space; strip leading/trailing.
    # We use a manual split/join to avoid any regex with backtracking risk.
    text = " ".join(text.split())

    # ── 5. Length cap ─────────────────────────────────────────────────────
    was_truncated = len(text) > MAX_INPUT_CHARS
    if was_truncated:
        text = text[:MAX_INPUT_CHARS]

    # ── 6. Empty / whitespace-only guard ──────────────────────────────────
    if not text:
        return SanitizeResult(text="", ok=False, was_truncated=was_truncated,
                              error="Please describe your concern.")

    return SanitizeResult(text=text, ok=True, was_truncated=was_truncated,
                          error="")
