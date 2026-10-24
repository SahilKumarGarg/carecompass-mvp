"""Refresh all last_verified dates in data/services.json to within the last 30 days.

For demo use only.  Distributes dates randomly so results look natural rather
than all being the same date.  Safe to re-run at any time.

Usage:
    python scripts/refresh_dates.py
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

_SERVICES_PATH = Path(__file__).parent.parent / "data" / "services.json"
_WINDOW_DAYS = 30   # all refreshed dates will be within this many days of today


def refresh_dates(path: Path = _SERVICES_PATH, window_days: int = _WINDOW_DAYS) -> int:
    """Rewrite every last_verified to a random date within the last *window_days* days.

    Args:
        path: Path to services.json.
        window_days: Max age in days for refreshed dates (default 30).

    Returns:
        Number of services updated.
    """
    today = date.today()

    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)

    services: list[dict] = data["services"]
    for svc in services:
        offset = random.randint(0, window_days)
        svc["last_verified"] = (today - timedelta(days=offset)).isoformat()

    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    return len(services)


if __name__ == "__main__":
    count = refresh_dates()
    print(f"Updated last_verified for {count} services (dates within the last {_WINDOW_DAYS} days).")
