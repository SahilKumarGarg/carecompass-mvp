"""scripts/summarize_user_test.py

Reads docs/user_test/results.csv and prints a summary of the user-test results.

Expected columns (case-insensitive header):
  participant  – any non-empty string
  case         – any non-empty string
  completed    – "yes" or "no" (case-insensitive)
  minutes      – positive number (int or float)
  know_next    – integer 1–5
  clear        – integer 1–5
  comment      – free text (may be empty)

Usage:
    python scripts/summarize_user_test.py
    python scripts/summarize_user_test.py path/to/other.csv
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

_REQUIRED_COLUMNS = {"participant", "case", "completed", "minutes",
                     "know_next", "clear", "comment"}


@dataclass
class Row:
    participant: str
    case: str
    completed: bool
    minutes: float
    know_next: int
    clear: int
    comment: str


@dataclass
class ParseResult:
    rows: list[Row] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _normalise_header(raw: list[str]) -> list[str]:
    return [h.strip().lower() for h in raw]


def _parse_csv(path: Path) -> ParseResult:
    result = ParseResult()

    try:
        text = path.read_text(encoding="utf-8-sig")   # handle BOM
    except FileNotFoundError:
        result.errors.append(f"File not found: {path}")
        return result
    except OSError as exc:
        result.errors.append(f"Cannot read file: {exc}")
        return result

    reader = csv.DictReader(text.splitlines())

    # Column presence check
    if reader.fieldnames is None:
        result.errors.append("File is empty — no header row found.")
        return result

    header = _normalise_header(list(reader.fieldnames))
    missing = _REQUIRED_COLUMNS - set(header)
    if missing:
        result.errors.append(
            f"Missing columns: {', '.join(sorted(missing))}"
        )
        return result

    for lineno, raw_row in enumerate(reader, start=2):   # 1-based; header=1
        # Normalise keys
        row_dict = {k.strip().lower(): v.strip() for k, v in raw_row.items()
                    if k is not None}

        participant = row_dict.get("participant", "")
        case        = row_dict.get("case", "")
        completed_s = row_dict.get("completed", "")
        minutes_s   = row_dict.get("minutes", "")
        know_s      = row_dict.get("know_next", "")
        clear_s     = row_dict.get("clear", "")
        comment     = row_dict.get("comment", "")

        row_errors: list[str] = []

        # participant
        if not participant:
            row_errors.append("'participant' is empty")

        # case
        if not case:
            row_errors.append("'case' is empty")

        # completed
        if completed_s.lower() not in ("yes", "no"):
            row_errors.append(
                f"'completed' must be 'yes' or 'no', got {completed_s!r}"
            )
            completed_bool: Optional[bool] = None
        else:
            completed_bool = completed_s.lower() == "yes"

        # minutes
        try:
            minutes_val = float(minutes_s)
            if minutes_val <= 0:
                raise ValueError
        except ValueError:
            row_errors.append(
                f"'minutes' must be a positive number, got {minutes_s!r}"
            )
            minutes_val = None

        # know_next
        try:
            know_val = int(know_s)
            if not 1 <= know_val <= 5:
                raise ValueError
        except ValueError:
            row_errors.append(
                f"'know_next' must be 1–5, got {know_s!r}"
            )
            know_val = None

        # clear
        try:
            clear_val = int(clear_s)
            if not 1 <= clear_val <= 5:
                raise ValueError
        except ValueError:
            row_errors.append(
                f"'clear' must be 1–5, got {clear_s!r}"
            )
            clear_val = None

        if row_errors:
            prefix = f"Row {lineno} (participant={participant!r}, case={case!r})"
            for e in row_errors:
                result.errors.append(f"{prefix}: {e}")
        else:
            result.rows.append(Row(
                participant=participant,
                case=case,
                completed=completed_bool,       # type: ignore[arg-type]
                minutes=minutes_val,            # type: ignore[arg-type]
                know_next=know_val,             # type: ignore[arg-type]
                clear=clear_val,                # type: ignore[arg-type]
                comment=comment,
            ))

    return result


# ---------------------------------------------------------------------------
# Summarise
# ---------------------------------------------------------------------------

def summarise(rows: list[Row]) -> dict:
    """Compute summary statistics from a validated list of rows.

    Returns a dict with keys:
        participants, total_tasks, completed_count, completed_pct,
        avg_know_next, avg_clear, avg_minutes, comments
    """
    if not rows:
        return {
            "participants": 0,
            "total_tasks": 0,
            "completed_count": 0,
            "completed_pct": 0.0,
            "avg_know_next": None,
            "avg_clear": None,
            "avg_minutes": None,
            "comments": [],
        }

    participants = len({r.participant for r in rows})
    total        = len(rows)
    completed    = sum(1 for r in rows if r.completed)

    avg_know  = sum(r.know_next for r in rows) / total
    avg_clear = sum(r.clear     for r in rows) / total
    avg_mins  = sum(r.minutes   for r in rows) / total

    comments = [
        {"participant": r.participant, "case": r.case, "comment": r.comment}
        for r in rows
        if r.comment.strip()
    ]

    return {
        "participants": participants,
        "total_tasks": total,
        "completed_count": completed,
        "completed_pct": completed / total * 100,
        "avg_know_next": round(avg_know,  1),
        "avg_clear":     round(avg_clear, 1),
        "avg_minutes":   round(avg_mins,  1),
        "comments": comments,
    }


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_report(stats: dict, errors: list[str]) -> str:
    lines: list[str] = []

    if errors:
        lines.append("VALIDATION ERRORS")
        lines.append("-" * 40)
        for e in errors:
            lines.append(f"  ✗ {e}")
        lines.append("")

    lines.append("USER TEST RESULTS SUMMARY")
    lines.append("=" * 40)

    if stats["total_tasks"] == 0:
        lines.append("No valid data rows — nothing to summarise.")
        return "\n".join(lines)

    n   = stats["participants"]
    tot = stats["total_tasks"]
    c   = stats["completed_count"]
    pct = stats["completed_pct"]

    lines.append(f"Participants           : {n}")
    lines.append(
        f"Tasks completed unaided: {c} of {tot} ({pct:.0f}%)"
    )
    lines.append(
        f"Avg 'I know what to do next'  : {stats['avg_know_next']:.1f} / 5"
    )
    lines.append(
        f"Avg 'explanation clear'        : {stats['avg_clear']:.1f} / 5"
    )
    lines.append(
        f"Avg minutes per task           : {stats['avg_minutes']:.1f}"
    )

    coms = stats["comments"]
    lines.append("")
    if coms:
        lines.append(f"Comments ({len(coms)}):")
        lines.append("-" * 40)
        for com in coms:
            lines.append(
                f"  [{com['participant']} / {com['case']}]  {com['comment']}"
            )
    else:
        lines.append("Comments: none recorded.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(csv_path: Optional[Path] = None) -> None:
    if csv_path is None:
        default = (
            Path(__file__).parent.parent
            / "docs" / "user_test" / "results.csv"
        )
        csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else default

    result = _parse_csv(csv_path)
    stats  = summarise(result.rows)
    report = format_report(stats, result.errors)
    print(report)


if __name__ == "__main__":
    main()
