"""Audit logger: writes every step and decision to logs/audit.jsonl.

Each line is a JSON object with at minimum:
  {"ts": "<iso-timestamp>", "session_id": "...", "step": "...", "data": {...}}

The logs/ directory is created automatically if absent.

Privacy:
  When LOG_USER_TEXT=false (default), free-text fields derived from user input
  are never written to the log in full.  Affected fields are replaced by their
  length and a short hash (first 8 hex chars of sha256).
  This covers: user_text, user_text_preview, concern, who, severity_cues,
  denied_symptoms, the "area" field in location_error steps, and raw error
  strings.  For location-level fields (state/county/city/zip) only the level
  name is logged, not the values.  Phone numbers are never logged regardless
  of this setting.  Newlines in any logged string are replaced with a space.

  With LOG_USER_TEXT=true the original behaviour is preserved (all text kept;
  phones still redacted).

To write to a different file (e.g. benchmark.jsonl), call log_to() directly.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import uuid
from pathlib import Path

_LOGS_DIR = Path(__file__).parent.parent / "logs"
_AUDIT_FILE = _LOGS_DIR / "audit.jsonl"
BENCHMARK_FILE = _LOGS_DIR / "benchmark.jsonl"

# Fields whose string values contain free text extracted from the user's input.
# When LOG_USER_TEXT=false these are replaced by (len, hash) pairs.
_FREE_TEXT_FIELDS = frozenset({
    "user_text",
    "user_text_preview",
    "concern",
    "who",
    "area",          # used in location_error steps — holds raw user location input
})

# List-of-strings fields containing free text extracted from user input.
_FREE_TEXT_LIST_FIELDS = frozenset({
    "severity_cues",
    "denied_symptoms",
})

# Location value keys — when LOG_USER_TEXT=false, log only which level is
# present, not the actual values.
_LOCATION_VALUE_KEYS = frozenset({"state", "county", "city", "zip"})

# Pattern for phone-number-like strings: 10 or 11 digits with optional
# separators (spaces, dashes, dots, parens).  Must have exactly 10 or 11
# contiguous digits once separators are removed.  A lookahead/lookbehind
# prevents matching standalone 5-digit ZIP codes, hospital IDs, session IDs,
# or short numeric values.
#
# The character class uses [\d().\s-] — note the hyphen is LAST in the class
# so it is treated as a literal hyphen and not as a range operator.
_PHONE_RE = re.compile(
    r"(?<!\d)"                 # not preceded by a digit
    r"[\d().\s-]{10,20}"      # 10-20 chars of digits and separators
    r"(?!\d)"                  # not followed by a digit
)


def _phone_digits(s: str) -> int:
    """Return the count of digit characters in *s*."""
    return sum(1 for c in s if c.isdigit())


def _redact_phones(s: str) -> str:
    """Replace phone-number-like substrings with [REDACTED].

    Only replaces matches that contain exactly 10 or 11 digits (i.e. a real
    North American phone number), so ZIP codes, hospital IDs and distances are
    left unchanged.
    """
    def _replace(m: re.Match) -> str:
        digits = _phone_digits(m.group(0))
        if digits in (10, 11):
            return "[REDACTED]"
        return m.group(0)

    return _PHONE_RE.sub(_replace, s)


def _log_user_text_enabled() -> bool:
    """Return True when LOG_USER_TEXT env var is set to a truthy value."""
    val = os.environ.get("LOG_USER_TEXT", "false").strip().lower()
    return val in ("1", "true", "yes")


def _strip_newlines(s: str) -> str:
    """Replace CR/LF sequences with a single space."""
    return s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def _safe_str(value: str) -> str:
    """Strip newlines and redact phone numbers from a string value."""
    return _redact_phones(_strip_newlines(value))


def _hash_text(raw: str) -> dict:
    """Return a dict with message_len and msg_hash for *raw*."""
    return {
        "len": len(raw),
        "hash": hashlib.sha256(raw.encode()).hexdigest()[:8],
    }


def _redact_data(data: dict) -> dict:
    """Return a copy of *data* with all free-text fields privacy-protected.

    When LOG_USER_TEXT=false (the default):
    - Free-text string fields (user_text, concern, who, area, …) are replaced
      by {"len": N, "hash": "xxxxxxxx"}.
    - user_text_preview is dropped entirely.
    - Free-text list fields (severity_cues, denied_symptoms) have each element
      replaced by its hash; the list length is preserved.
    - Location value keys (state, county, city, zip) are replaced by a single
      "location_level" key whose value names the finest level present.
    - Keys named "error" have their string value replaced by the exception
      class name (first word before space or colon) — the full message is
      dropped.
    - All remaining string values have newlines stripped and phones redacted.
    """
    result: dict = {}
    location_levels_present: list[str] = []

    for k, v in data.items():
        # ── user_text_preview → always dropped ──────────────────────────────
        if k == "user_text_preview":
            continue

        # ── free-text string fields → (len, hash) ────────────────────────────
        if k in _FREE_TEXT_FIELDS:
            raw = str(v)
            suffix = "text" if k == "user_text" else k
            result[f"{suffix}_len"] = len(raw)
            result[f"{suffix}_hash"] = hashlib.sha256(raw.encode()).hexdigest()[:8]
            # Keep the legacy keys for user_text for backward-compatibility.
            if k == "user_text":
                result["message_len"] = len(raw)
                result["msg_hash"] = hashlib.sha256(raw.encode()).hexdigest()[:8]
                # Remove the duplicate suffixed keys for user_text
                del result["text_len"]
                del result["text_hash"]
            continue

        # ── free-text list fields → hashed list ──────────────────────────────
        if k in _FREE_TEXT_LIST_FIELDS:
            if isinstance(v, list):
                result[k] = [
                    {"len": len(str(item)), "hash": hashlib.sha256(str(item).encode()).hexdigest()[:8]}
                    if isinstance(item, str) else item
                    for item in v
                ]
            else:
                result[k] = v
            continue

        # ── location value keys → collect level names only ───────────────────
        if k in _LOCATION_VALUE_KEYS:
            if v:  # only record levels that actually have a value
                location_levels_present.append(k)
            continue

        # ── error strings → log only the error type, not the message ────────
        if k == "error" and isinstance(v, str):
            # Extract first word before ": " or space — that's the class name
            # or the short error type token (e.g. "no_state_in_area", "ValueError").
            type_token = re.split(r"[:\s]", v.strip(), maxsplit=1)[0]
            result[k] = _strip_newlines(type_token)
            continue

        # ── regular string fields → newlines + phone redaction ───────────────
        if isinstance(v, str):
            result[k] = _safe_str(v)
            continue

        # ── list of strings → newlines + phone redaction per element ─────────
        if isinstance(v, list):
            result[k] = [
                _safe_str(item) if isinstance(item, str) else item
                for item in v
            ]
            continue

        # ── everything else (int, bool, float, dict, …) → pass through ───────
        result[k] = v

    # Emit the location level (finest one present), if any location keys were seen.
    if location_levels_present:
        # Order from finest to coarsest; pick the finest present.
        _LEVEL_ORDER = ["zip", "city", "county", "state"]
        finest = next((lvl for lvl in _LEVEL_ORDER if lvl in location_levels_present), location_levels_present[0])
        result["location_level"] = finest

    return result


def _ensure_logs_dir() -> None:
    """Create logs/ if it does not exist."""
    _LOGS_DIR.mkdir(exist_ok=True)


def new_session_id() -> str:
    """Return a fresh UUID4 string for a new session."""
    return str(uuid.uuid4())


def _write(path: Path, session_id: str, step: str, data: dict) -> None:
    """Append one JSON line to *path*."""
    _ensure_logs_dir()

    if _log_user_text_enabled():
        # Strip newlines from all string values; never log phone numbers.
        safe_data: dict = {
            k: (_safe_str(v) if isinstance(v, str) else v)
            for k, v in data.items()
        }
    else:
        safe_data = _redact_data(data)

    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "session_id": session_id,
        "step": _strip_newlines(step),
        "data": safe_data,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def log(session_id: str, step: str, data: dict) -> None:
    """Append one JSON line to logs/audit.jsonl.

    Args:
        session_id: Unique identifier for the current user session.
        step: Name of the pipeline step (e.g. "safety", "find", "present").
        data: Arbitrary dict of data to record for this step.
    """
    _write(_AUDIT_FILE, session_id, step, data)


def log_benchmark(session_id: str, step: str, data: dict) -> None:
    """Append one JSON line to logs/benchmark.jsonl (W8-6).

    Benchmark runs use this instead of log() so that test traffic does not
    pollute the operational audit log.

    Args:
        session_id: Unique identifier for the benchmark scenario session.
        step: Step name (e.g. "benchmark_run", "benchmark_summary").
        data: Arbitrary dict of data to record.
    """
    _write(BENCHMARK_FILE, session_id, step, data)
