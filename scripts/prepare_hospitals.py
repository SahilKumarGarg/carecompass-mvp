"""Prepare hospitals data from the raw Kaggle US Hospitals CSV.

Reads  data/raw/Hospitals.csv  (all columns as text) and writes
data/hospitals_clean.csv with only OPEN hospitals, cleaned columns,
and valid coordinates.

Usage:
    python scripts/prepare_hospitals.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

_RAW_PATH = Path(__file__).parent.parent / "data" / "raw" / "Hospitals.csv"
_OUT_PATH = Path(__file__).parent.parent / "data" / "hospitals_clean.csv"

# Values treated as missing in the source
_MISSING = {"NOT AVAILABLE", "-999", ""}

# Output columns in order
_OUT_COLS = [
    "ID",
    "NAME",          # upper-cased for matching
    "display_name",  # title-case for display
    "ADDRESS",
    "CITY",          # upper-cased for matching
    "STATE",         # upper-cased for matching
    "ZIP",
    "TELEPHONE",
    "TYPE",
    "COUNTY",        # upper-cased for matching
    "COUNTYFIPS",
    "LATITUDE",
    "LONGITUDE",
    "BEDS",
    "TRAUMA",
    "HELIPAD",
    "WEBSITE",
    "OWNER",
    "VAL_DATE",      # year only
    "SOURCEDATE",    # year only
]


def _clean(value: str) -> str:
    """Strip whitespace; return empty string for missing sentinels."""
    v = value.strip()
    return "" if v in _MISSING else v


def _year_only(value: str) -> str:
    """Extract the 4-digit year from a date string like '2021/10/19 00:00:00+00'."""
    v = _clean(value)
    if not v:
        return ""
    return v[:4]


def _zip5(value: str) -> str:
    """Return 5-char zero-padded ZIP; empty if missing."""
    v = _clean(value)
    if not v:
        return ""
    # Keep only digits then zero-pad to 5
    digits = v.replace("-", "").split("-")[0]  # e.g. "06001-1234" → "06001"
    # More robustly: just take raw ZIP field (not ZIP4) — already 5-char raw
    # But source may have stripped leading zeros; restore them.
    raw = v.zfill(5)[:5]
    return raw


def _float_or_empty(value: str) -> str:
    """Return float string if parseable, else empty."""
    v = _clean(value)
    if not v:
        return ""
    try:
        return str(float(v))
    except ValueError:
        return ""


def _int_or_empty(value: str) -> str:
    """Return integer string if parseable and > 0, else empty."""
    v = _clean(value)
    if not v:
        return ""
    try:
        n = int(float(v))
        return "" if n < 0 else str(n)
    except ValueError:
        return ""


def prepare(raw_path: Path = _RAW_PATH, out_path: Path = _OUT_PATH) -> dict:
    """Run the full preparation pipeline.

    Returns:
        dict with keys rows_in, closed_removed, rows_out, missing_counts.
    """
    rows_in = 0
    closed_removed = 0
    seen_ids: set[str] = set()
    dup_removed = 0
    out_rows: list[dict] = []

    with raw_path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            rows_in += 1

            # Keep only OPEN hospitals
            if raw.get("STATUS", "").strip() != "OPEN":
                closed_removed += 1
                continue

            hosp_id = _clean(raw.get("ID", ""))

            # Drop duplicate IDs (keep first occurrence)
            if hosp_id in seen_ids:
                dup_removed += 1
                continue
            seen_ids.add(hosp_id)

            lat = _float_or_empty(raw.get("LATITUDE", ""))
            lon = _float_or_empty(raw.get("LONGITUDE", ""))

            # Drop rows without valid coordinates
            if not lat or not lon:
                continue

            raw_name = _clean(raw.get("NAME", ""))
            row: dict[str, str] = {
                "ID":           hosp_id,
                "NAME":         raw_name.upper(),
                "display_name": raw_name.title(),
                "ADDRESS":      _clean(raw.get("ADDRESS", "")),
                "CITY":         _clean(raw.get("CITY", "")).upper(),
                "STATE":        _clean(raw.get("STATE", "")).upper(),
                "ZIP":          _zip5(raw.get("ZIP", "")),
                "TELEPHONE":    _clean(raw.get("TELEPHONE", "")),
                "TYPE":         _clean(raw.get("TYPE", "")),
                "COUNTY":       _clean(raw.get("COUNTY", "")).upper(),
                "COUNTYFIPS":   _clean(raw.get("COUNTYFIPS", "")),
                "LATITUDE":     lat,
                "LONGITUDE":    lon,
                "BEDS":         _int_or_empty(raw.get("BEDS", "")),
                "TRAUMA":       _clean(raw.get("TRAUMA", "")),
                "HELIPAD":      _clean(raw.get("HELIPAD", "")),
                "WEBSITE":      _clean(raw.get("WEBSITE", "")),
                "OWNER":        _clean(raw.get("OWNER", "")),
                "VAL_DATE":     _year_only(raw.get("VAL_DATE", "")),
                "SOURCEDATE":   _year_only(raw.get("SOURCEDATE", "")),
            }
            out_rows.append(row)

    # Write output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_OUT_COLS)
        writer.writeheader()
        writer.writerows(out_rows)

    # Missing-value counts per output column
    missing_counts: dict[str, int] = {
        col: sum(1 for r in out_rows if r[col] == "") for col in _OUT_COLS
    }

    return {
        "rows_in": rows_in,
        "closed_removed": closed_removed,
        "dup_removed": dup_removed,
        "rows_out": len(out_rows),
        "missing_counts": missing_counts,
    }


def _print_report(stats: dict) -> None:
    print("=" * 50)
    print("Hospital data preparation report")
    print("=" * 50)
    print(f"  Rows in (raw)      : {stats['rows_in']}")
    print(f"  Closed removed     : {stats['closed_removed']}")
    print(f"  Duplicate IDs      : {stats['dup_removed']}")
    print(f"  Rows out (clean)   : {stats['rows_out']}")
    print()
    print("  Missing values per column:")
    for col, n in stats["missing_counts"].items():
        if n:
            print(f"    {col:<14}: {n}")
    print("=" * 50)


if __name__ == "__main__":
    if not _RAW_PATH.exists():
        print(f"ERROR: raw file not found: {_RAW_PATH}", file=sys.stderr)
        sys.exit(1)
    stats = prepare()
    _print_report(stats)
    print(f"Written: {_OUT_PATH}")
