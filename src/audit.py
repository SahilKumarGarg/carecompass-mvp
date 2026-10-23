"""Audit logger: writes every step and decision to logs/audit.jsonl.

Each line is a JSON object with at minimum:
  {"ts": "<iso-timestamp>", "session_id": "...", "step": "...", "data": {...}}

The logs/ directory is created automatically if absent.
"""

from __future__ import annotations

import datetime
import json
import uuid
from pathlib import Path

_LOGS_DIR = Path(__file__).parent.parent / "logs"
_AUDIT_FILE = _LOGS_DIR / "audit.jsonl"


def _ensure_logs_dir() -> None:
    """Create logs/ if it does not exist."""
    _LOGS_DIR.mkdir(exist_ok=True)


def new_session_id() -> str:
    """Return a fresh UUID4 string for a new session."""
    return str(uuid.uuid4())


def log(session_id: str, step: str, data: dict) -> None:
    """Append one JSON line to logs/audit.jsonl.

    Args:
        session_id: Unique identifier for the current user session.
        step: Name of the pipeline step (e.g. "safety", "find", "present").
        data: Arbitrary dict of data to record for this step.
    """
    _ensure_logs_dir()
    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "session_id": session_id,
        "step": step,
        "data": data,
    }
    with _AUDIT_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
