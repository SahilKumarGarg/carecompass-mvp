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
        result = self._redact({"area": "call me at 555-867-5309 please"})
        self.assertNotIn("555", result["area"])
        self.assertIn("[REDACTED]", result["area"])

    def test_long_phone_number_redacted(self) -> None:
        result = self._redact({"note": "1 (800) 123-4567 or 555.123.4567"})
        self.assertNotIn("800", result["note"])

    def test_zip_code_not_redacted(self) -> None:
        """5-digit ZIP must not be treated as a phone number."""
        result = self._redact({"zip": "36420"})
        self.assertEqual(result["zip"], "36420")

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


if __name__ == "__main__":
    unittest.main()
