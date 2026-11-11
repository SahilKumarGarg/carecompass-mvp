"""Log retention utility — scripts/clear_logs.py

Deletes *.jsonl and *.log files from the logs/ directory.

Usage:
    python scripts/clear_logs.py           # dry-run: list files to delete
    python scripts/clear_logs.py --delete  # actually delete the files

Retention policy (from docs/GOVERNANCE.md):
  Audit logs must not be kept longer than 90 days.
  Run this script (with --delete) periodically or automate via cron/task
  scheduler.

Safety:
  - Only files inside the fixed logs/ directory are touched.
  - The logs/ directory itself is never removed.
  - This script never traverses outside the project root.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_LOGS_DIR = _ROOT / "logs"


def _find_log_files() -> list[Path]:
    """Return all *.jsonl and *.log files inside logs/."""
    files: list[Path] = []
    for pattern in ("*.jsonl", "*.log"):
        files.extend(_LOGS_DIR.glob(pattern))
    return sorted(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Clear CareCompass audit log files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Retention policy: logs must not be kept longer than 90 days.\n"
            "Run with --delete to actually remove the files."
        ),
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete the listed files (default is dry-run only).",
    )
    args = parser.parse_args(argv)

    if not _LOGS_DIR.exists():
        print(f"Logs directory does not exist: {_LOGS_DIR}")
        return 0

    files = _find_log_files()
    if not files:
        print("No log files found.")
        return 0

    total_bytes = sum(f.stat().st_size for f in files)
    print(f"Found {len(files)} log file(s) ({total_bytes:,} bytes):")
    for f in files:
        size = f.stat().st_size
        print(f"  {f.relative_to(_ROOT)}  ({size:,} bytes)")

    if not args.delete:
        print(
            "\nDry-run complete. Pass --delete to remove the files listed above."
        )
        return 0

    deleted = 0
    errors = 0
    for f in files:
        try:
            f.unlink()
            print(f"  Deleted: {f.relative_to(_ROOT)}")
            deleted += 1
        except OSError as exc:
            print(f"  ERROR deleting {f.relative_to(_ROOT)}: {exc}", file=sys.stderr)
            errors += 1

    print(f"\n{deleted} file(s) deleted, {errors} error(s).")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
