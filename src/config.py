"""Configuration validation for CareCompass.

Validates environment variables and fixed data-file paths at startup.
Call validate_config() once during application startup; it raises
ConfigError with a clear message on the first problem found.

Rules:
- DATA_SOURCE must be exactly "hospitals_us" or "demo".
- EMERGENCY_NUMBER must match digits only, 3–6 characters.
- Data files are loaded from fixed paths derived from this file's location;
  user input never influences which file is opened.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_VALID_DATA_SOURCES = frozenset({"hospitals_us", "demo"})
_EMERGENCY_NUMBER_RE = re.compile(r"^\d{3,6}$")

# Fixed data-file paths — never derived from user input.
_DATA_DIR = Path(__file__).parent.parent / "data"
_REQUIRED_FILES: dict[str, list[str]] = {
    "hospitals_us": ["hospitals_clean.csv", "messages.json", "red_flags.json", "banned_phrases.json"],
    "demo":         ["services.json",       "messages.json", "red_flags.json", "banned_phrases.json"],
}


class ConfigError(RuntimeError):
    """Raised when the application configuration is invalid."""


def _data_source() -> str:
    return os.environ.get("DATA_SOURCE", "hospitals_us").strip().lower()


def _emergency_number() -> str:
    return os.environ.get("EMERGENCY_NUMBER", "").strip()


def validate_config() -> None:
    """Validate all required configuration at startup.

    Checks performed:
    1. DATA_SOURCE is exactly "hospitals_us" or "demo".
    2. EMERGENCY_NUMBER (if set) matches digits only, 3–6 characters.
    3. Required data files exist at their fixed paths.

    Raises:
        ConfigError: on the first configuration problem found.
    """
    source = _data_source()
    if source not in _VALID_DATA_SOURCES:
        raise ConfigError(
            f"DATA_SOURCE={source!r} is not allowed. "
            f"Must be one of: {sorted(_VALID_DATA_SOURCES)}. "
            "Check your .env file."
        )

    number = _emergency_number()
    if number and not _EMERGENCY_NUMBER_RE.match(number):
        raise ConfigError(
            f"EMERGENCY_NUMBER={number!r} is invalid. "
            "Must contain only digits and be 3–6 characters long (e.g. '911', '999', '112'). "
            "Check your .env file."
        )

    for filename in _REQUIRED_FILES.get(source, []):
        path = _DATA_DIR / filename
        if not path.exists():
            raise ConfigError(
                f"Required data file not found: {path}. "
                f"DATA_SOURCE={source!r} requires this file to be present."
            )
