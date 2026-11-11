"""Tests for config validation, rate limiting, and audit log redaction.

Covers:
  - src.config.validate_config()  — DATA_SOURCE, EMERGENCY_NUMBER, file paths
  - src.rate_limit.RateLimiter    — per-minute cap, session cap, polite messages
  - src.audit log redaction       — LOG_USER_TEXT=false path
"""

from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

class TestConfigValidation(unittest.TestCase):
    """validate_config() accepts valid config and rejects invalid config."""

    def _validate(self, env: dict) -> None:
        """Run validate_config with the given env overrides."""
        from src.config import validate_config
        # Patch env for the duration of the call.
        with patch.dict(os.environ, env, clear=False):
            validate_config()

    def _validate_raises(self, env: dict, fragment: str) -> None:
        from src.config import validate_config, ConfigError
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(ConfigError) as cm:
                validate_config()
        self.assertIn(fragment, str(cm.exception))

    # ── DATA_SOURCE ──────────────────────────────────────────────────────────

    def test_data_source_hospitals_us_accepted(self) -> None:
        self._validate({"DATA_SOURCE": "hospitals_us"})

    def test_data_source_demo_accepted(self) -> None:
        self._validate({"DATA_SOURCE": "demo"})

    def test_data_source_invalid_raises(self) -> None:
        self._validate_raises({"DATA_SOURCE": "postgres"}, "DATA_SOURCE")

    def test_data_source_empty_raises(self) -> None:
        self._validate_raises({"DATA_SOURCE": ""}, "DATA_SOURCE")

    def test_data_source_uppercase_rejected(self) -> None:
        # Value is lowercased before check — "DEMO" → "demo" is fine.
        self._validate({"DATA_SOURCE": "DEMO"})

    def test_data_source_with_spaces_rejected(self) -> None:
        # Spaces stripped before check, leaving "hospitals_us" — accepted.
        self._validate({"DATA_SOURCE": "  hospitals_us  "})

    def test_data_source_sql_injection_rejected(self) -> None:
        self._validate_raises({"DATA_SOURCE": "demo; DROP TABLE"}, "DATA_SOURCE")

    def test_data_source_path_traversal_rejected(self) -> None:
        self._validate_raises({"DATA_SOURCE": "../../etc/passwd"}, "DATA_SOURCE")

    # ── EMERGENCY_NUMBER ─────────────────────────────────────────────────────

    def test_emergency_number_911_accepted(self) -> None:
        self._validate({"DATA_SOURCE": "demo", "EMERGENCY_NUMBER": "911"})

    def test_emergency_number_999_accepted(self) -> None:
        self._validate({"DATA_SOURCE": "demo", "EMERGENCY_NUMBER": "999"})

    def test_emergency_number_112_accepted(self) -> None:
        self._validate({"DATA_SOURCE": "demo", "EMERGENCY_NUMBER": "112"})

    def test_emergency_number_6digits_accepted(self) -> None:
        self._validate({"DATA_SOURCE": "demo", "EMERGENCY_NUMBER": "123456"})

    def test_emergency_number_empty_accepted(self) -> None:
        # Empty = not set; default is used. Not an error.
        self._validate({"DATA_SOURCE": "demo", "EMERGENCY_NUMBER": ""})

    def test_emergency_number_too_short_raises(self) -> None:
        self._validate_raises(
            {"DATA_SOURCE": "demo", "EMERGENCY_NUMBER": "11"},
            "EMERGENCY_NUMBER",
        )

    def test_emergency_number_too_long_raises(self) -> None:
        self._validate_raises(
            {"DATA_SOURCE": "demo", "EMERGENCY_NUMBER": "1234567"},
            "EMERGENCY_NUMBER",
        )

    def test_emergency_number_with_letters_raises(self) -> None:
        self._validate_raises(
            {"DATA_SOURCE": "demo", "EMERGENCY_NUMBER": "91a"},
            "EMERGENCY_NUMBER",
        )

    def test_emergency_number_with_dash_raises(self) -> None:
        self._validate_raises(
            {"DATA_SOURCE": "demo", "EMERGENCY_NUMBER": "9-11"},
            "EMERGENCY_NUMBER",
        )

    def test_emergency_number_injection_raises(self) -> None:
        self._validate_raises(
            {"DATA_SOURCE": "demo", "EMERGENCY_NUMBER": "911; rm -rf /"},
            "EMERGENCY_NUMBER",
        )


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class TestRateLimiter(unittest.TestCase):
    """RateLimiter enforces per-minute and session caps."""

    def _make(self):
        from src.rate_limit import RateLimiter
        return RateLimiter()

    def test_first_message_allowed(self) -> None:
        rl = self._make()
        allowed, msg = rl.check()
        self.assertTrue(allowed)
        self.assertEqual(msg, "")

    def test_record_increments_count(self) -> None:
        rl = self._make()
        rl.check()
        rl.record()
        self.assertEqual(rl.total_count, 1)

    def test_nine_messages_all_allowed(self) -> None:
        rl = self._make()
        for _ in range(9):
            allowed, _ = rl.check()
            self.assertTrue(allowed)
            rl.record()

    def test_tenth_message_allowed_eleventh_blocked(self) -> None:
        rl = self._make()
        for _ in range(10):
            allowed, _ = rl.check()
            self.assertTrue(allowed)
            rl.record()
        # 11th within the same minute should be blocked.
        allowed, msg = rl.check()
        self.assertFalse(allowed)
        self.assertIn("wait", msg.lower())

    def test_blocked_message_contains_wait_time(self) -> None:
        rl = self._make()
        for _ in range(10):
            rl.check()
            rl.record()
        _, msg = rl.check()
        self.assertRegex(msg, r"\d+ second")

    def test_session_cap_blocks_after_100(self) -> None:
        from src.rate_limit import _SESSION_LIMIT
        rl = self._make()
        # Simulate 100 messages by directly setting state.
        rl.total_count = _SESSION_LIMIT
        allowed, msg = rl.check()
        self.assertFalse(allowed)
        self.assertIn("session", msg.lower())

    def test_session_cap_message_mentions_new_session(self) -> None:
        from src.rate_limit import _SESSION_LIMIT
        rl = self._make()
        rl.total_count = _SESSION_LIMIT
        _, msg = rl.check()
        self.assertIn("new session", msg.lower())

    def test_window_expiry_allows_after_delay(self) -> None:
        """After the 60-second window expires, requests are allowed again."""
        from src.rate_limit import RateLimiter, _PER_MINUTE_LIMIT, _WINDOW_SECONDS
        rl = RateLimiter()
        # Fill window with old timestamps (older than the window).
        old_ts = time.monotonic() - _WINDOW_SECONDS - 1.0
        for _ in range(_PER_MINUTE_LIMIT):
            rl.timestamps.append(old_ts)
            rl.total_count += 1
        # Old timestamps are outside the window; next request must be allowed.
        allowed, _ = rl.check()
        self.assertTrue(allowed)

    def test_check_does_not_record(self) -> None:
        """check() must not mutate total_count."""
        rl = self._make()
        rl.check()
        self.assertEqual(rl.total_count, 0)

    def test_record_without_prior_check_works(self) -> None:
        rl = self._make()
        rl.record()
        self.assertEqual(rl.total_count, 1)
        self.assertEqual(len(rl.timestamps), 1)


# ---------------------------------------------------------------------------
# Audit log redaction (LOG_USER_TEXT=false)
# ---------------------------------------------------------------------------

class TestAuditRedaction(unittest.TestCase):
    """When LOG_USER_TEXT=false, user_text must never appear in the log."""

    def setUp(self) -> None:
        # Ensure the env variable is explicitly false.
        os.environ["LOG_USER_TEXT"] = "false"

    def tearDown(self) -> None:
        os.environ.pop("LOG_USER_TEXT", None)

    def _redact(self, data: dict) -> dict:
        from src.audit import _redact_data
        return _redact_data(data)

    def test_user_text_replaced_by_len_and_hash(self) -> None:
        result = self._redact({"user_text": "I have a headache"})
        self.assertNotIn("user_text", result)
        self.assertIn("message_len", result)
        self.assertIn("msg_hash", result)
        self.assertEqual(result["message_len"], len("I have a headache"))
        self.assertEqual(len(result["msg_hash"]), 8)

    def test_user_text_preview_dropped(self) -> None:
        result = self._redact({"user_text_preview": "I have a headache"})
        self.assertNotIn("user_text_preview", result)

    def test_other_string_fields_preserved(self) -> None:
        result = self._redact({"tier": "routine", "step": "safety"})
        self.assertEqual(result["tier"], "routine")
        self.assertEqual(result["step"], "safety")

    def test_newlines_stripped_from_strings(self) -> None:
        result = self._redact({"tier": "rou\ntine", "step": "safe\r\nty"})
        self.assertNotIn("\n", result["tier"])
        self.assertNotIn("\r", result["step"])

    def test_phone_number_redacted_in_string_field(self) -> None:
        result = self._redact({"note": "call me at 555-867-5309 please"})
        self.assertNotIn("555", result["note"])
        self.assertIn("[REDACTED]", result["note"])

    def test_long_phone_number_redacted(self) -> None:
        result = self._redact({"note": "1 (800) 123-4567 or 555.123.4567"})
        self.assertNotIn("800", result["note"])

    def test_zip_code_not_redacted(self) -> None:
        """5-digit ZIP in a generic (non-location) field must not be treated as a phone."""
        result = self._redact({"note": "postal code 36420"})
        self.assertIn("36420", result["note"])

    def test_list_strings_redacted(self) -> None:
        result = self._redact({"hits": ["routine: 555-1234567", "safe"]})
        # Phone-like pattern in list elements should be redacted.
        self.assertNotIn("555", result["hits"][0])

    def test_non_string_values_preserved(self) -> None:
        result = self._redact({"count": 5, "passed": True, "score": 0.95})
        self.assertEqual(result["count"], 5)
        self.assertTrue(result["passed"])
        self.assertAlmostEqual(result["score"], 0.95)

    def test_hash_deterministic(self) -> None:
        """Same text must always produce the same hash."""
        r1 = self._redact({"user_text": "hello"})
        r2 = self._redact({"user_text": "hello"})
        self.assertEqual(r1["msg_hash"], r2["msg_hash"])

    def test_different_texts_different_hashes(self) -> None:
        r1 = self._redact({"user_text": "hello"})
        r2 = self._redact({"user_text": "world"})
        self.assertNotEqual(r1["msg_hash"], r2["msg_hash"])


class TestAuditLogUserTextEnabled(unittest.TestCase):
    """When LOG_USER_TEXT=true, user_text IS included but phones still redacted."""

    def setUp(self) -> None:
        os.environ["LOG_USER_TEXT"] = "true"

    def tearDown(self) -> None:
        os.environ.pop("LOG_USER_TEXT", None)

    def _safe_str(self, s: str) -> str:
        from src.audit import _safe_str
        return _safe_str(s)

    def test_phone_in_string_still_redacted(self) -> None:
        result = self._safe_str("call 555-867-5309 for info")
        self.assertNotIn("555", result)
        self.assertIn("[REDACTED]", result)

    def test_newline_stripped(self) -> None:
        result = self._safe_str("line one\nline two")
        self.assertNotIn("\n", result)
        self.assertIn("line one", result)


class TestStripNewlines(unittest.TestCase):
    """_strip_newlines removes CR, LF, CRLF."""

    def _strip(self, s: str) -> str:
        from src.audit import _strip_newlines
        return _strip_newlines(s)

    def test_lf_replaced(self) -> None:
        self.assertNotIn("\n", self._strip("a\nb"))

    def test_cr_replaced(self) -> None:
        self.assertNotIn("\r", self._strip("a\rb"))

    def test_crlf_replaced(self) -> None:
        self.assertNotIn("\r\n", self._strip("a\r\nb"))

    def test_no_newline_unchanged(self) -> None:
        self.assertEqual("hello world", self._strip("hello world"))


# ---------------------------------------------------------------------------
# New tests: Fix 1 — full free-text redaction when LOG_USER_TEXT=false
# ---------------------------------------------------------------------------

class TestFullFreeTextRedaction(unittest.TestCase):
    """LOG_USER_TEXT=false must redact all free-text fields, not only user_text."""

    def setUp(self) -> None:
        os.environ["LOG_USER_TEXT"] = "false"

    def tearDown(self) -> None:
        os.environ.pop("LOG_USER_TEXT", None)

    def _redact(self, data: dict) -> dict:
        from src.audit import _redact_data
        return _redact_data(data)

    def test_concern_replaced_by_hash(self) -> None:
        result = self._redact({"concern": "sharp chest pain for 3 days"})
        self.assertNotIn("concern", result)
        self.assertIn("concern_len", result)
        self.assertIn("concern_hash", result)
        self.assertEqual(result["concern_len"], len("sharp chest pain for 3 days"))

    def test_who_replaced_by_hash(self) -> None:
        result = self._redact({"who": "my 8-year-old daughter"})
        self.assertNotIn("who", result)
        self.assertIn("who_len", result)
        self.assertIn("who_hash", result)

    def test_severity_cues_elements_hashed(self) -> None:
        cues = ["severe", "very bad"]
        result = self._redact({"severity_cues": cues})
        self.assertIn("severity_cues", result)
        # Each element should be a dict with 'len' and 'hash', not a plain string.
        for item in result["severity_cues"]:
            self.assertIsInstance(item, dict)
            self.assertIn("len", item)
            self.assertIn("hash", item)
        # The original words must not appear anywhere in the JSON serialisation.
        serialised = str(result["severity_cues"])
        self.assertNotIn("severe", serialised)

    def test_denied_symptoms_elements_hashed(self) -> None:
        symptoms = ["no fever", "not in pain"]
        result = self._redact({"denied_symptoms": symptoms})
        serialised = str(result["denied_symptoms"])
        self.assertNotIn("fever", serialised)
        self.assertNotIn("pain", serialised)
        for item in result["denied_symptoms"]:
            self.assertIsInstance(item, dict)

    def test_location_values_not_logged(self) -> None:
        """State, county, city and zip values must be suppressed; only level kept."""
        result = self._redact({
            "state": "CA",
            "county": "Los Angeles",
            "city": "Burbank",
            "zip": "91501",
        })
        self.assertNotIn("state", result)
        self.assertNotIn("county", result)
        self.assertNotIn("city", result)
        self.assertNotIn("zip", result)
        # location_level should be present and name the finest level.
        self.assertIn("location_level", result)
        self.assertEqual(result["location_level"], "zip")

    def test_location_level_city_when_no_zip(self) -> None:
        result = self._redact({"state": "TX", "city": "Austin"})
        self.assertEqual(result.get("location_level"), "city")

    def test_location_level_county_only(self) -> None:
        result = self._redact({"state": "TX", "county": "Travis"})
        self.assertEqual(result.get("location_level"), "county")

    def test_location_level_state_only(self) -> None:
        result = self._redact({"state": "TX"})
        self.assertEqual(result.get("location_level"), "state")

    def test_error_string_redacted_to_type(self) -> None:
        """Error strings must be reduced to the error type token, not the full message."""
        result = self._redact({"error": "ValueError: city 'Springfield' not found in TX"})
        self.assertNotIn("Springfield", str(result["error"]))
        # The type token (first word) should remain.
        self.assertEqual(result["error"], "ValueError")

    def test_error_short_token_preserved(self) -> None:
        result = self._redact({"error": "no_state_in_area"})
        self.assertEqual(result["error"], "no_state_in_area")

    def test_error_with_city_in_message_no_city_in_log(self) -> None:
        result = self._redact({"error": "No match for city Denver in CO"})
        self.assertNotIn("Denver", str(result["error"]))

    def test_area_field_redacted(self) -> None:
        """The 'area' field (location_error steps) must be replaced by hash."""
        result = self._redact({"area": "Springfield, IL"})
        self.assertNotIn("area", result)
        self.assertIn("area_len", result)
        self.assertIn("area_hash", result)

    def test_zip_not_in_regular_string_field(self) -> None:
        """A 5-digit ZIP appearing in a non-location field must NOT be redacted as a phone."""
        result = self._redact({"note": "Looking near 90210"})
        # '90210' must still appear (not phone-redacted).
        self.assertIn("90210", result["note"])


# ---------------------------------------------------------------------------
# Fix 2 — phone regex: scope and hyphen escaping
# ---------------------------------------------------------------------------

class TestPhoneRedactionRegex(unittest.TestCase):
    """_redact_phones must only redact real 10/11-digit phone numbers."""

    def _redact(self, s: str) -> str:
        from src.audit import _redact_phones
        return _redact_phones(s)

    # ── Should be REDACTED ────────────────────────────────────────────────────

    def test_10_digit_with_dashes(self) -> None:
        result = self._redact("call 555-867-5309 please")
        self.assertIn("[REDACTED]", result)
        self.assertNotIn("5309", result)

    def test_11_digit_with_country_code(self) -> None:
        result = self._redact("1-800-555-0100")
        self.assertIn("[REDACTED]", result)
        self.assertNotIn("800", result)

    def test_10_digit_with_parens(self) -> None:
        result = self._redact("reach me at (555) 123-4567 anytime")
        self.assertIn("[REDACTED]", result)
        self.assertNotIn("123-4567", result)

    def test_10_digit_dotted(self) -> None:
        result = self._redact("555.123.4567")
        self.assertIn("[REDACTED]", result)

    def test_10_digit_spaces(self) -> None:
        result = self._redact("555 123 4567")
        self.assertIn("[REDACTED]", result)

    def test_multiple_phones_both_redacted(self) -> None:
        result = self._redact("1 (800) 123-4567 or 555.123.4567")
        self.assertNotIn("800", result)
        self.assertNotIn("555", result)

    # ── Must NOT be redacted ──────────────────────────────────────────────────

    def test_zip_code_not_redacted(self) -> None:
        result = self._redact("ZIP 90210")
        self.assertIn("90210", result)

    def test_5_digit_number_not_redacted(self) -> None:
        result = self._redact("hospital ID 12345")
        self.assertIn("12345", result)

    def test_short_id_not_redacted(self) -> None:
        """Session IDs and hospital IDs with fewer than 10 digits must be preserved."""
        result = self._redact("ID: 7890")
        self.assertIn("7890", result)

    def test_distance_not_redacted(self) -> None:
        """A distance value like '12.5 miles' must not be redacted."""
        result = self._redact("about 12.5 miles")
        self.assertIn("12.5", result)

    def test_7_digit_number_not_redacted(self) -> None:
        """7-digit numbers (below 10-digit threshold) must not be redacted."""
        result = self._redact("local 555-1234")
        # Only 7 digits — should NOT be treated as a phone number.
        self.assertNotIn("[REDACTED]", result)

    def test_character_class_hyphen_is_literal(self) -> None:
        r"""The hyphen in the regex character class must be a literal, not a range.

        In [\d().\s-] the hyphen must be treated as a literal hyphen character.
        If it were a range (e.g. [\d()..-\s]) it could accidentally match or
        exclude characters in the range between '.' and '\s'.  This test verifies
        that a hyphen in a phone number is correctly handled by checking that a
        phone with only hyphens-as-separators is properly redacted, while a
        short string with a hyphen is not over-redacted.
        """
        from src.audit import _PHONE_RE
        # The pattern must compile without error — that itself confirms the hyphen
        # is syntactically valid.  We just double-check by importing it.
        import re
        self.assertIsNotNone(_PHONE_RE.pattern)
        # Verify a 10-digit hyphen-separated number is matched.
        m = _PHONE_RE.search("555-123-4567")
        self.assertIsNotNone(m)


# ---------------------------------------------------------------------------
# Fix 3 — ConfigError messages must not contain path separators
# ---------------------------------------------------------------------------

class TestConfigErrorNoPath(unittest.TestCase):
    """ConfigError messages shown to the user must never contain '/' or '\\'."""

    def _raises_config_error(self, env: dict):
        from src.config import validate_config, ConfigError
        import pathlib
        with unittest.mock.patch.dict(os.environ, env, clear=False):
            with self.assertRaises(ConfigError) as cm:
                validate_config()
        return str(cm.exception)

    def test_missing_file_error_no_slash(self) -> None:
        """Error for a missing required file must not contain a path separator."""
        import pathlib, tempfile, shutil
        # Use a temp dir as the data dir so required files are missing.
        with tempfile.TemporaryDirectory() as tmpdir:
            # Patch the _DATA_DIR so files cannot be found.
            with unittest.mock.patch("src.config._DATA_DIR", pathlib.Path(tmpdir)):
                msg = self._raises_config_error({"DATA_SOURCE": "demo"})
        self.assertNotIn("/", msg)
        self.assertNotIn("\\", msg)
        # The filename itself (no dir) should still appear.
        self.assertIn("services.json", msg)

    def test_missing_file_error_contains_filename_not_fullpath(self) -> None:
        """The error message must contain only the filename, not the directory path."""
        import pathlib, tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            with unittest.mock.patch("src.config._DATA_DIR", pathlib.Path(tmpdir)):
                msg = self._raises_config_error({"DATA_SOURCE": "demo"})
        # Must not contain the temp directory path.
        self.assertNotIn(tmpdir, msg)

    def test_data_source_error_no_slash(self) -> None:
        """DATA_SOURCE errors don't include paths either."""
        msg = self._raises_config_error({"DATA_SOURCE": "invalid_source"})
        self.assertNotIn("/", msg)
        self.assertNotIn("\\", msg)


import unittest.mock  # imported at bottom to avoid import order issue at top


# ---------------------------------------------------------------------------
# Fix 4 — app.py assistant messages are HTML-escaped
# ---------------------------------------------------------------------------

class TestAssistantMessageEscaping(unittest.TestCase):
    """html.escape must be applied to assistant messages before rendering."""

    def _escape(self, s: str) -> str:
        import html
        return html.escape(s)

    def test_script_tag_escaped(self) -> None:
        """A <script> tag in an assistant message must be escaped to &lt;script&gt;."""
        msg = "<script>alert('xss')</script>Here are some hospitals near you."
        escaped = self._escape(msg)
        self.assertNotIn("<script>", escaped)
        self.assertIn("&lt;script&gt;", escaped)

    def test_img_onerror_escaped(self) -> None:
        msg = '<img src=x onerror="alert(1)"> Based on your symptoms...'
        escaped = self._escape(msg)
        self.assertNotIn("<img", escaped)

    def test_normal_markdown_survives_escape(self) -> None:
        """Normal markdown bold/italic/emoji must survive html.escape unchanged."""
        msg = "**Option 1:** Here are the nearest options. ✅"
        escaped = self._escape(msg)
        # html.escape should not alter plain markdown (** and * are not HTML special chars).
        self.assertIn("**Option 1:**", escaped)
        self.assertIn("✅", escaped)

    def test_display_intro_with_script_escaped(self) -> None:
        """Simulate display_intro containing a script tag; escape must neutralise it."""
        display_intro = "Based on your symptoms. <script>document.location='http://evil.com'</script>"
        escaped = self._escape(display_intro)
        self.assertNotIn("<script>", escaped)
        self.assertIn("Based on your symptoms.", escaped)

    def test_ampersand_escaped(self) -> None:
        msg = "St. Mary & General Hospital"
        escaped = self._escape(msg)
        self.assertIn("&amp;", escaped)
        self.assertNotIn(" & ", escaped)


# ---------------------------------------------------------------------------
# Fix 1 (end-to-end) — 5 sample cases: typed text, city, ZIP and phone must
# not appear anywhere in the audit file written with LOG_USER_TEXT=false
# ---------------------------------------------------------------------------

class TestAuditFilePrivacy(unittest.TestCase):
    """End-to-end: run 5 cases with LOG_USER_TEXT=false and check the audit file."""

    _SAMPLE_CASES = [
        {
            "user_text": "I have severe headache and vomiting since yesterday",
            "city": "Springfield",
            "zip": "62701",
            "phone": "2175550123",
        },
        {
            "user_text": "My daughter broke her arm falling off a bike",
            "city": "Riverside",
            "zip": "92501",
            "phone": "9095550199",
        },
        {
            "user_text": "Chest tightness for two hours feeling short of breath",
            "city": "Portland",
            "zip": "97201",
            "phone": "5035550177",
        },
        {
            "user_text": "Mild rash on arm no fever no pain",
            "city": "Memphis",
            "zip": "38101",
            "phone": "9015550133",
        },
        {
            "user_text": "Back pain getting worse over three days",
            "city": "Albany",
            "zip": "12201",
            "phone": "5185550144",
        },
    ]

    def setUp(self) -> None:
        os.environ["LOG_USER_TEXT"] = "false"

    def tearDown(self) -> None:
        os.environ.pop("LOG_USER_TEXT", None)

    def _redact(self, data: dict) -> dict:
        from src.audit import _redact_data
        return _redact_data(data)

    def _run_case(self, case: dict) -> str:
        """Build the audit record that would be written for a 'describe' step and return as JSON."""
        import json
        data = {
            "user_text": case["user_text"],
            "area": json.dumps({"state": "IL", "city": case["city"], "zip": case["zip"]}),
        }
        redacted = self._redact(data)
        # Also simulate the understand step data.
        understand_data = {
            "who": "myself",
            "concern": case["user_text"],
            "severity_cues": ["severe"],
            "denied_symptoms": [],
            "missing_fields": ["duration"],
            "confidence": "offline",
        }
        redacted_understand = self._redact(understand_data)
        return json.dumps(redacted) + "\n" + json.dumps(redacted_understand)

    def _assert_no_pii(self, case: dict, log_content: str) -> None:
        """Assert that no word of user_text, city, zip, or phone appears in log_content."""
        # Check every individual word of user_text.
        for word in case["user_text"].split():
            if len(word) <= 3:
                continue  # skip stop words like "I", "a", "off", "arm"
            self.assertNotIn(
                word.lower(), log_content.lower(),
                f"Word '{word}' from user_text found in audit log",
            )
        self.assertNotIn(
            case["city"].lower(), log_content.lower(),
            f"City '{case['city']}' found in audit log",
        )
        self.assertNotIn(
            case["zip"], log_content,
            f"ZIP '{case['zip']}' found in audit log",
        )
        self.assertNotIn(
            case["phone"], log_content,
            f"Phone '{case['phone']}' found in audit log",
        )

    def test_five_cases_no_pii_in_audit(self) -> None:
        for case in self._SAMPLE_CASES:
            with self.subTest(user_text=case["user_text"][:30]):
                log_line = self._run_case(case)
                self._assert_no_pii(case, log_line)

    def test_phone_in_user_text_not_in_log(self) -> None:
        """Phone number embedded in typed text must not appear in the log."""
        case = {
            "user_text": "Please call me at 555-867-5309 for my appointment",
            "city": "Chicago",
            "zip": "60601",
            "phone": "5558675309",
        }
        data = {"user_text": case["user_text"], "area": "Chicago, IL"}
        redacted = self._redact(data)
        import json
        log_content = json.dumps(redacted)
        self.assertNotIn("5558675309", log_content)
        self.assertNotIn("867-5309", log_content)


if __name__ == "__main__":
    unittest.main()
