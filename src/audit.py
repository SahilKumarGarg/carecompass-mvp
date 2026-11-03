"""Audit logger: writes every step and decision to logs/audit.jsonl.

Each line is a JSON object with at minimum:
  {"ts": "<iso-timestamp>", "session_id": "...", "step": "...", "data": {...}}

The logs/ directory is created automatically if absent.

To write to a different file (e.g. benchmark.jsonl), call log_to() directly.
"""

from __future__ import annotations

import datetime
import json
import uuid
from pathlib import Path

_LOGS_DIR = Path(__file__).parent.parent / "logs"
_AUDIT_FILE = _LOGS_DIR / "audit.jsonl"
BENCHMARK_FILE = _LOGS_DIR / "benchmark.jsonl"


def _ensure_logs_dir() -> None:
    """Create logs/ if it does not exist."""
    _LOGS_DIR.mkdir(exist_ok=True)


def new_session_id() -> str:
    """Return a fresh UUID4 string for a new session."""
    return str(uuid.uuid4())


def _write(path: Path, session_id: str, step: str, data: dict) -> None:
    """Append one JSON line to *path*."""
    _ensure_logs_dir()
    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "session_id": session_id,
        "step": step,
        "data": data,
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
