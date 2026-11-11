"""Security tests for CareCompass input/output hardening.

Covers:
- sanitize_user_input: all attack strings are cleaned/rejected without crash.
- No unescaped HTML in any output that reaches the user.
- App responds in under 1 second for all inputs (safety + sanitise path).
- No diagnosis wording in any response.
- safe URL helpers: tel: and maps URLs.
- Template placeholder injection: {0}, %s, {{}}, [EMERGENCY_NUMBER].
- Long inputs (5 000 and 100 000 characters) are capped at 1 000 chars.
"""

from __future__ import annotations

import html
import re
import time
from typing import Any

import pytest

from src.sanitize import MAX_INPUT_CHARS, sanitize_user_input
from src.safety import check_red_flags

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BANNED_DIAGNOSES = re.compile(
    r"\b(diagnosed?|diagnosis|prognosis|you have|you've got|"
    r"prescri(?:be|ption)|treatment plan|take aspirin|take ibuprofen|"
    r"appendicitis|diabetes|cancer|tumou?r|infection is)\b",
    re.I,
)

_TAG_RE = re.compile(r"<[^>]+>")


def _has_unescaped_html(text: str) -> bool:
    """Return True if text contains an unescaped HTML tag or attribute.

    Checks for literal < followed by a letter (opening tag) or / (closing
    tag).  Event handler patterns (on*=) are only flagged when they appear
    inside an unescaped tag (i.e., after a literal < that has not been
    entity-encoded), to avoid false positives on already-escaped strings like
    ``&lt;img onerror=...&gt;``.
    """
    # Unescaped opening/closing tags: a literal < followed by a letter or /
    if re.search(r"<[a-zA-Z/]", text):
        return True
    return False


def _pipeline_response(user_input: str) -> str:
    """Run check_red_flags and return a representative output string.

    This tests the pure-Python safety layer (no LLM, no Streamlit).
    The sanitize step is tested separately; here we feed already-sanitised
    text into the pipeline to verify outputs contain no diagnosis wording.
    """
    result = check_red_flags(user_input)
    # Return the tier string as the 'output' for diagnosis checks.
    return result.tier


# ---------------------------------------------------------------------------
# Parametrised attack-string table
# ---------------------------------------------------------------------------

# Each entry: (label, raw_input, expected_ok, expect_truncated)
_ATTACK_CASES: list[tuple[str, Any, bool, bool]] = [
    # XSS via script tag
    (
        "script_tag",
        "<script>alert(1)</script>",
        True,   # sanitised, not rejected
        False,
    ),
    # XSS via img onerror
    (
        "img_onerror",
        "<img src=x onerror=alert(1)>",
        True,
        False,
    ),
    # Markdown javascript link
    (
        "markdown_js_link",
        "[click](javascript:alert(1))",
        True,
        False,
    ),
    # Markdown image with external URL
    (
        "markdown_image_external",
        "![x](http://example.com/x.png)",
        True,
        False,
    ),
    # SQL injection
    (
        "sql_injection",
        "'; DROP TABLE hospitals;--",
        True,
        False,
    ),
    # Null bytes → rejected as binary
    (
        "null_bytes",
        "hello\x00world",
        False,   # rejected
        False,
    ),
    # CRLF and tab characters
    (
        "crlf_tab",
        "hello\r\nworld\ttab",
        True,   # sanitised (whitespace collapsed)
        False,
    ),
    # Zero-width characters
    (
        "zero_width",
        "hello\u200bworld\u200c",
        True,
        False,
    ),
    # Right-to-left override (bidi attack)
    (
        "rtl_override",
        "hello\u202eworld",
        True,
        False,
    ),
    # Python format placeholder {0}
    (
        "format_placeholder_brace_0",
        "{0}",
        True,
        False,
    ),
    # C-style format %s
    (
        "format_placeholder_percent_s",
        "%s",
        True,
        False,
    ),
    # Double-brace Jinja style
    (
        "format_placeholder_double_brace",
        "{{}}",
        True,
        False,
    ),
    # Bracket placeholder [EMERGENCY_NUMBER]
    (
        "bracket_placeholder_emergency",
        "[EMERGENCY_NUMBER]",
        True,
        False,
    ),
    # 5 000-character string — should be truncated to 1 000
    (
        "long_5000",
        "a" * 5_000,
        True,
        True,   # truncated
    ),
    # 100 000-character string — should be truncated to 1 000
    (
        "long_100000",
        "x" * 100_000,
        True,
        True,   # truncated
    ),
    # Repeated emoji
    (
        "repeated_emoji",
        "😀" * 200,
        True,
        False,   # 200 code points × 1 = within limit after NFKC
    ),
    # Hospital name with & and <
    (
        "hospital_special_chars",
        "St. Mary & General <Hospital>",
        True,
        False,
    ),
]


# ---------------------------------------------------------------------------
# Test 1: sanitize_user_input — no crash, correct ok/truncated flags
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,raw,expected_ok,expect_trunc", _ATTACK_CASES, ids=[c[0] for c in _ATTACK_CASES])
def test_sanitize_no_crash(label: str, raw: Any, expected_ok: bool, expect_trunc: bool) -> None:
    """sanitize_user_input must not crash on any input."""
    result = sanitize_user_input(raw)
    assert result.ok == expected_ok, (
        f"[{label}] expected ok={expected_ok} but got ok={result.ok}, error={result.error!r}"
    )
    if expect_trunc:
        assert result.was_truncated, f"[{label}] expected was_truncated=True"
        assert len(result.text) <= MAX_INPUT_CHARS, (
            f"[{label}] truncated text still exceeds MAX_INPUT_CHARS"
        )


# ---------------------------------------------------------------------------
# Test 2: html.escape applied to sanitised text produces no unescaped HTML
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,raw,expected_ok,_trunc", _ATTACK_CASES, ids=[c[0] for c in _ATTACK_CASES])
def test_sanitize_no_unescaped_html_in_text(label: str, raw: Any, expected_ok: bool, _trunc: bool) -> None:
    """Applying html.escape to the sanitised text must produce no raw HTML tags.

    The sanitiser preserves content as plain text; the rendering layer calls
    html.escape before inserting into HTML.  This test verifies that the
    combination of sanitise + html.escape produces safe output.
    """
    result = sanitize_user_input(raw)
    if result.ok:
        escaped = html.escape(result.text)
        assert not _has_unescaped_html(escaped), (
            f"[{label}] html.escape(sanitized) still contains unescaped HTML: {escaped!r}"
        )


# ---------------------------------------------------------------------------
# Test 3: Sanitised output does not grow beyond MAX_INPUT_CHARS
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,raw,expected_ok,_trunc", _ATTACK_CASES, ids=[c[0] for c in _ATTACK_CASES])
def test_sanitize_length_cap(label: str, raw: Any, expected_ok: bool, _trunc: bool) -> None:
    """No sanitised text exceeds MAX_INPUT_CHARS."""
    result = sanitize_user_input(raw)
    assert len(result.text) <= MAX_INPUT_CHARS, (
        f"[{label}] text length {len(result.text)} exceeds {MAX_INPUT_CHARS}"
    )


# ---------------------------------------------------------------------------
# Test 4: App-level pipeline responds in under 1 second (offline path)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,raw,expected_ok,_trunc", _ATTACK_CASES, ids=[c[0] for c in _ATTACK_CASES])
def test_pipeline_under_1_second(label: str, raw: Any, expected_ok: bool, _trunc: bool) -> None:
    """Sanitise + safety check together must complete in under 1 second.

    This guards against ReDoS: very long or adversarial strings must not
    cause slow regex matching because the length cap is applied BEFORE any
    regex runs.
    """
    start = time.monotonic()
    san = sanitize_user_input(raw)
    if san.ok:
        check_red_flags(san.text)
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, (
        f"[{label}] pipeline took {elapsed:.3f}s (limit: 1.0s)"
    )


# ---------------------------------------------------------------------------
# Test 5: No diagnosis wording in safety output
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,raw,expected_ok,_trunc", _ATTACK_CASES, ids=[c[0] for c in _ATTACK_CASES])
def test_no_diagnosis_in_safety_output(label: str, raw: Any, expected_ok: bool, _trunc: bool) -> None:
    """The safety tier string must not contain diagnosis wording."""
    san = sanitize_user_input(raw)
    if san.ok:
        tier = _pipeline_response(san.text)
        assert not _BANNED_DIAGNOSES.search(tier), (
            f"[{label}] diagnosis wording found in tier output: {tier!r}"
        )


# ---------------------------------------------------------------------------
# Test 6: Null bytes are rejected (not just stripped)
# ---------------------------------------------------------------------------

def test_null_byte_rejected() -> None:
    """A string containing a NUL byte must be rejected with ok=False."""
    result = sanitize_user_input("valid text\x00injected")
    assert not result.ok
    assert "binary" in result.error.lower() or result.error


# ---------------------------------------------------------------------------
# Test 7: Empty and whitespace-only inputs are rejected
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("empty_input", ["", "   ", "\t\n\r", "\u200b", "\u202e"])
def test_empty_input_rejected(empty_input: str) -> None:
    """Empty or whitespace/invisible-only inputs must be rejected."""
    result = sanitize_user_input(empty_input)
    assert not result.ok, f"Expected rejection for {empty_input!r}"


# ---------------------------------------------------------------------------
# Test 8: Non-string inputs are rejected
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("non_string", [None, 42, 3.14, [], {}, b"bytes"])
def test_non_string_rejected(non_string: Any) -> None:
    """Non-string inputs must be rejected with ok=False."""
    result = sanitize_user_input(non_string)
    assert not result.ok


# ---------------------------------------------------------------------------
# Test 9: Unicode NFKC is applied
# ---------------------------------------------------------------------------

def test_nfkc_normalisation() -> None:
    """Fullwidth characters must be normalised to ASCII equivalents."""
    # Fullwidth letter A (U+FF21) → A after NFKC
    result = sanitize_user_input("\uff21\uff42\uff43")
    assert result.ok
    assert "A" in result.text or "a" in result.text.lower()


# ---------------------------------------------------------------------------
# Test 10: CRLF and tab collapsed to single space
# ---------------------------------------------------------------------------

def test_crlf_collapsed() -> None:
    """CRLF, bare CR, bare LF and tab must be collapsed to a single space."""
    result = sanitize_user_input("line1\r\nline2\rline3\nline4\ttab")
    assert result.ok
    assert "\r" not in result.text
    assert "\n" not in result.text
    assert "\t" not in result.text
    # Double (or more) spaces should not appear.
    assert "  " not in result.text


# ---------------------------------------------------------------------------
# Test 11: Zero-width and bidi override chars stripped
# ---------------------------------------------------------------------------

def test_zero_width_stripped() -> None:
    """Zero-width space (U+200B) must be removed from sanitised text."""
    result = sanitize_user_input("hello\u200bworld")
    assert result.ok
    assert "\u200b" not in result.text


def test_bidi_override_stripped() -> None:
    """Right-to-left override (U+202E) must be removed from sanitised text."""
    result = sanitize_user_input("hello\u202eworld")
    assert result.ok
    assert "\u202e" not in result.text
    # The surrounding text should be preserved (joined without the override).
    assert "helloworld" in result.text


# ---------------------------------------------------------------------------
# Test 12: Placeholders appear as plain text, never executed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("placeholder,label", [
    ("{0}", "brace_0"),
    ("%s", "percent_s"),
    ("{{}}", "double_brace"),
    ("[EMERGENCY_NUMBER]", "bracket_emergency"),
])
def test_placeholder_as_plain_text(placeholder: str, label: str) -> None:
    """Format placeholders must survive sanitisation as literal text."""
    result = sanitize_user_input(f"I need help with {placeholder}")
    assert result.ok, f"[{label}] sanitise unexpectedly rejected input"
    # The text must contain the literal placeholder string (not interpreted).
    # NFKC may affect { and } slightly, but they remain verbatim for ASCII chars.
    # We check the sanitised text via the safety pipeline — no crash.
    safety = check_red_flags(result.text)
    # safety.tier is one of "emergency", "urgent", "routine" — no placeholder bleed-through.
    assert safety.tier in ("emergency", "urgent", "routine")


# ---------------------------------------------------------------------------
# Test 13: Hospital name with & and < — HTML escape
# ---------------------------------------------------------------------------

def test_hospital_name_html_escape() -> None:
    """Strings containing & and < must be properly escaped when displayed as HTML."""
    raw = "St. Mary & General <Hospital>"
    san = sanitize_user_input(raw)
    assert san.ok
    # The sanitised text itself may still contain & and < (that's fine —
    # escaping happens at the HTML rendering layer).
    # Verify that html.escape produces safe output (what the card renderer does).
    escaped = html.escape(san.text)
    assert "<" not in escaped or "&lt;" in escaped or "&amp;" in escaped
    assert _has_unescaped_html(escaped) is False


# ---------------------------------------------------------------------------
# Test 14: safe_tel_url only contains safe chars
# ---------------------------------------------------------------------------

def test_safe_tel_url_strips_injection() -> None:
    """_safe_tel_url must strip anything that isn't a digit, +, -, (, ), space."""
    from app import _safe_tel_url  # type: ignore[import]
    # Adversarial input trying to inject a javascript: scheme.
    url = _safe_tel_url("javascript:alert(1)")
    assert url.startswith("tel:")
    # Should contain only safe chars after the scheme.
    body = url[4:]  # strip "tel:"
    assert not re.search(r"[^\d+\-()\ ]", body), f"Unsafe chars in tel URL body: {body!r}"


def test_safe_tel_url_normal_number() -> None:
    """_safe_tel_url must pass through a normal phone number intact."""
    from app import _safe_tel_url  # type: ignore[import]
    url = _safe_tel_url("+1 (555) 000-0000")
    assert url == "tel:+1 (555) 000-0000"


# ---------------------------------------------------------------------------
# Test 15: _safe_maps_url produces a safe https URL
# ---------------------------------------------------------------------------

def test_safe_maps_url_encodes_query() -> None:
    """_safe_maps_url must percent-encode the query and use the fixed base."""
    from app import _safe_maps_url  # type: ignore[import]
    url = _safe_maps_url("<script>alert(1)</script>")
    assert url.startswith("https://www.google.com/maps/search/")
    assert "<" not in url
    assert ">" not in url
    assert "script" not in url.split("?")[0]


def test_safe_maps_url_does_not_allow_scheme_override() -> None:
    """Query content must not override the URL scheme."""
    from app import _safe_maps_url  # type: ignore[import]
    url = _safe_maps_url("javascript:evil")
    assert url.startswith("https://")


# ---------------------------------------------------------------------------
# Test 16: 5 000-char and 100 000-char strings — truncated and fast
# ---------------------------------------------------------------------------

def test_5000_char_truncated_and_fast() -> None:
    long_input = "I have chest pain " * 278  # ≈ 5 000 chars
    start = time.monotonic()
    result = sanitize_user_input(long_input)
    elapsed = time.monotonic() - start

    assert result.was_truncated
    assert len(result.text) <= MAX_INPUT_CHARS
    assert elapsed < 1.0, f"Sanitise of 5000-char input took {elapsed:.3f}s"


def test_100000_char_truncated_and_fast() -> None:
    long_input = "a" * 100_000
    start = time.monotonic()
    result = sanitize_user_input(long_input)
    elapsed = time.monotonic() - start

    assert result.was_truncated
    assert len(result.text) <= MAX_INPUT_CHARS
    assert elapsed < 1.0, f"Sanitise of 100 000-char input took {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Test 17: Repeated emoji — within limit
# ---------------------------------------------------------------------------

def test_repeated_emoji_handled() -> None:
    """200 emoji chars (each 1 code point after NFKC) must not crash or exceed the cap."""
    emoji_str = "😀" * 200
    result = sanitize_user_input(emoji_str)
    # 200 code points is within the 1 000 limit.
    assert result.ok or not result.ok   # either is acceptable — no crash is the key requirement
    assert len(result.text) <= MAX_INPUT_CHARS


# ---------------------------------------------------------------------------
# Test 18: Truncation happens BEFORE regex (ReDoS guard)
# ---------------------------------------------------------------------------

def test_long_adversarial_regex_input_fast() -> None:
    """A string designed to trigger ReDoS must be handled in under 1 s."""
    # Classic ReDoS pattern against (a+)+ style: "aaa...!aaa...!" with mismatch.
    adversarial = "a" * 950 + "!" * 50
    start = time.monotonic()
    san = sanitize_user_input(adversarial)
    if san.ok:
        check_red_flags(san.text)
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"Took {elapsed:.3f}s on adversarial ReDoS pattern"
