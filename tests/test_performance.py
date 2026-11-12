"""Performance tests for the hot-path safety checker.

Validates that check_red_flags() stays within the latency budget for the
worst-case input allowed by MAX_INPUT_CHARS in src/sanitize.py.

The worst-case input is 1,000 characters of near-miss tokens — each token is
one edit away from a real synonym (e.g. "chext" vs "chest", "painn" vs "pain")
so every rule attempts the fuzzy pass.  No rule fires, so every rule runs to
completion.  This exercises the full O(rules × groups × synonyms × tokens)
inner loop.

Budget: 200 ms on any developer machine.
"""

from __future__ import annotations

import time

from src.safety import check_red_flags
from src.sanitize import MAX_INPUT_CHARS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _worst_case_input() -> str:
    """Return a MAX_INPUT_CHARS string of near-miss tokens that fire no rule.

    Each token is chosen to be one edit away from a high-frequency synonym
    (forcing the fuzzy Levenshtein pass) but never close enough to satisfy
    ALL groups of any single rule simultaneously.  The conjunction requirement
    ensures no rule fires even though every token looks almost-relevant.

    Token vocabulary (edit-distance 1 from real synonyms, not the full word):
      "chext"  — chest  (RF001 group-1 near-miss)
      "painn"  — pain   (RF001 group-2 near-miss)
      "breth"  — breath (RF002 group-1 near-miss)
      "dizzyy" — dizzy  (RF007 near-miss)
      "feverr" — fever  (RF006 near-miss)

    None of these is an exact synonym, and the near-misses do NOT satisfy the
    conjunction for any single rule (e.g. "chext" alone cannot complete RF001
    because the second group also needs a near-miss in the same input, which
    is disallowed by the one-fuzzy-group constraint).
    """
    tokens = ["chext", "painn", "breth", "dizzyy", "feverr"]
    # Cycle through tokens until we have enough characters.
    parts: list[str] = []
    total = 0
    i = 0
    while total < MAX_INPUT_CHARS:
        tok = tokens[i % len(tokens)]
        # +1 for the trailing space separator
        if total + len(tok) + 1 > MAX_INPUT_CHARS:
            break
        parts.append(tok)
        total += len(tok) + 1
        i += 1
    text = " ".join(parts)
    assert len(text) <= MAX_INPUT_CHARS, (
        f"Generated input too long: {len(text)} > {MAX_INPUT_CHARS}"
    )
    # Must be close to the limit to be a genuine stress test.
    assert len(text) >= MAX_INPUT_CHARS - 20, (
        f"Generated input too short: {len(text)} < {MAX_INPUT_CHARS - 20}"
    )
    return text


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestWorstCaseLatency:
    """check_red_flags() must finish within 200 ms on a 1,000-char near-miss input."""

    _BUDGET_MS = 200

    def test_worst_case_returns_routine(self) -> None:
        """Near-miss tokens must NOT trigger any red-flag rule."""
        text = _worst_case_input()
        result = check_red_flags(text)
        assert result.tier == "routine", (
            f"Near-miss input unexpectedly fired rule(s): {result.hits}"
        )

    def test_worst_case_within_200ms(self) -> None:
        """1,000-character near-miss input must be processed in under 200 ms."""
        text = _worst_case_input()

        # Warm up: the first call may include any lazy-init work.
        check_red_flags(text)

        # Timed run.
        start = time.monotonic()
        check_red_flags(text)
        elapsed_ms = (time.monotonic() - start) * 1000

        assert elapsed_ms < self._BUDGET_MS, (
            f"check_red_flags() took {elapsed_ms:.1f} ms on a {len(text)}-char "
            f"worst-case input (budget: {self._BUDGET_MS} ms). "
            "Performance regression detected."
        )
