"""Audit logger: writes every step and decision to logs/audit.jsonl.

Each line is a JSON object with at minimum:
  {"ts": "<iso-timestamp>", "session_id": "...", "step": "...", "data": {...}}

The logs/ directory is created automatically if absent.

Privacy:
  When LOG_USER_TEXT=false (default), raw user text is never written to the log.
  Instead, message_len (int) and msg_hash (first 8 hex chars of sha256) are logged.
  Phone numbers typed by the user are never logged regardless of this setting.
  Newlines in any logged string are replaced with a space.

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

# Pattern for phone-number-like strings: 7+ digit clusters optionally separated
# by spaces, dashes, dots or parentheses.  Conservative: 7 digits minimum to
# avoid matching ZIP codes and short IDs.
_PHONE_RE = re.compile(r"[\d()\-\.\s]{7,}(?=\D|$)")


def _log_user_text_enabled() -> bool:
    """Return True when LOG_USER_TEXT env var is set to a truthy value."""
    val = os.environ.get("LOG_USER_TEXT", "false").strip().lower()
    return val in ("1", "true", "yes")


def _strip_newlines(s: str) -> str:
    """Replace CR/LF sequences with a single space."""
    return s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def _redact_phones(s: str) -> str:
    """Replace phone-number-like substrings with [REDACTED]."""
    return _PHONE_RE.sub("[REDACTED]", s)


def _safe_str(value: str) -> str:
    """Strip newlines and redact phone numbers from a string value."""
    return _redact_phones(_strip_newlines(value))


def _redact_data(data: dict) -> dict:
    """Return a copy of *data* with user_text replaced by privacy metadata.

    Fields affected:
    - ``user_text`` → removed; replaced by ``message_len`` and ``msg_hash``.
    - ``user_text_preview`` → removed entirely (could contain PII).
    - Any string value has newlines stripped and phone-like patterns redacted.
    """
    result: dict = {}
    for k, v in data.items():
        if k == "user_text":
            # Replace raw text with length + short hash only.
            raw = str(v)
            result["message_len"] = len(raw)
            result["msg_hash"] = hashlib.sha256(raw.encode()).hexdigest()[:8]
        elif k == "user_text_preview":
            # Drop preview entirely — it's a slice of user text.
            pass
        elif isinstance(v, str):
            result[k] = _safe_str(v)
        elif isinstance(v, list):
            result[k] = [
                _safe_str(item) if isinstance(item, str) else item
                for item in v
            ]
        else:
            result[k] = v
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
