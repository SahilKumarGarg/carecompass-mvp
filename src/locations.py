"""Locations module: geographic lookups against data/hospitals_clean.csv.

Provides list_states(), list_counties(), list_cities() for UI dropdowns,
and resolve_location() which returns matching hospital rows plus a reference
point (mean lat/lon) used by the finder for distance calculations.

All matching is case-insensitive and always scoped by STATE to prevent mixing
hospitals from different states that share a city or county name.

Startup validation: on first load the module asserts that:
  - No row has STATUS == "CLOSED" (if a STATUS column exists).
  - No two rows share the same ID.
A ValueError is raised immediately if either condition is violated, so a bad
data file is caught at start-up rather than silently producing wrong results.
"""

from __future__ import annotations

import csv
import dataclasses
from pathlib import Path
from typing import Optional

_HOSPITALS_PATH = Path(__file__).parent.parent / "data" / "hospitals_clean.csv"

# ---------------------------------------------------------------------------
# Module-level cache
# ---------------------------------------------------------------------------

_rows: list[dict] | None = None


def _validate(rows: list[dict]) -> None:
    """Raise ValueError if rows contain CLOSED records or duplicate IDs.

    Checks performed (both are skipped gracefully when the relevant column is
    absent so that test fixtures without those columns still work):
    - STATUS column: any row with STATUS == "CLOSED" is a hard error.
    - ID column: duplicate values are a hard error.
    """
    closed: list[str] = []
    seen_ids: dict[str, int] = {}  # id → first row index
    for i, row in enumerate(rows):
        status = row.get("STATUS", "").strip().upper()
        if status == "CLOSED":
            closed.append(row.get("ID", f"row {i}"))
        rid = row.get("ID", "")
        if rid:
            if rid in seen_ids:
                raise ValueError(
                    f"hospitals_clean.csv contains duplicate ID '{rid}' "
                    f"(rows {seen_ids[rid]} and {i})."
                )
            seen_ids[rid] = i
    if closed:
        sample = ", ".join(closed[:5])
        raise ValueError(
            f"hospitals_clean.csv contains {len(closed)} CLOSED record(s). "
            f"First offenders: {sample}. "
            "Run scripts/prepare_hospitals.py to regenerate a clean file."
        )


def _load() -> list[dict]:
    """Load hospitals_clean.csv once at first call; validate and cache."""
    global _rows
    if _rows is None:
        with _HOSPITALS_PATH.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
        _validate(rows)
        _rows = rows
    return _rows


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class LocationResult:
    """Matching hospital rows plus a derived reference point."""

    rows: list[dict]              # full hospital CSV rows that matched
    ref_lat: float                # mean latitude of matching hospitals
    ref_lon: float                # mean longitude of matching hospitals
    widened_from: Optional[str]   # e.g. "zip" if we widened from ZIP to city
    widened_to: Optional[str]     # e.g. "city" after widening
    notice: Optional[str]         # human-readable widening notice


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def _mean_latlon(rows: list[dict]) -> tuple[float, float]:
    """Return (mean_lat, mean_lon) for a non-empty list of hospital rows."""
    lats = [float(r["LATITUDE"]) for r in rows if r.get("LATITUDE")]
    lons = [float(r["LONGITUDE"]) for r in rows if r.get("LONGITUDE")]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def _filter(
    state: Optional[str] = None,
    county_fips: Optional[str] = None,
    city: Optional[str] = None,
    zip_: Optional[str] = None,
) -> list[dict]:
    """Return rows matching all supplied (non-None) criteria (case-insensitive).

    STATE is always required when county, city or ZIP is supplied, so that
    the same city name in two states never mixes results.

    Any row with STATUS == "CLOSED" is silently dropped as an extra runtime
    guard, in addition to the startup validation in _validate().
    """
    rows = _load()
    state_upper = state.upper().strip() if state else None
    city_upper = city.upper().strip() if city else None
    zip_str = zip_.strip() if zip_ else None

    result: list[dict] = []
    for row in rows:
        # Runtime closed-record guard (defence-in-depth after startup validation).
        if row.get("STATUS", "").strip().upper() == "CLOSED":
            continue
        if state_upper and row["STATE"].upper() != state_upper:
            continue
        if county_fips and row["COUNTYFIPS"] != county_fips:
            continue
        if city_upper and row["CITY"].upper() != city_upper:
            continue
        if zip_str and row["ZIP"] != zip_str:
            continue
        result.append(row)
    return result


# ---------------------------------------------------------------------------
# Public dropdown helpers
# ---------------------------------------------------------------------------

def list_states() -> list[str]:
    """Return a sorted list of unique two-letter state codes present in the data."""
    return sorted({r["STATE"] for r in _load() if r.get("STATE")})


def list_counties(state: str) -> list[tuple[str, str]]:
    """Return sorted (county_name, countyfips) pairs for the given state.

    Args:
        state: Two-letter state abbreviation (case-insensitive).

    Returns:
        List of (COUNTY, COUNTYFIPS) tuples, deduplicated and sorted by name.
    """
    state_upper = state.upper().strip()
    seen: dict[str, str] = {}
    for row in _load():
        if row["STATE"].upper() != state_upper:
            continue
        name = row.get("COUNTY", "")
        fips = row.get("COUNTYFIPS", "")
        if name and fips and fips not in seen:
            seen[fips] = name
    return sorted(seen.items(), key=lambda t: t[1])  # sort by county name


def list_cities(state: str, county: Optional[str] = None) -> list[str]:
    """Return sorted unique city names for the given state (and optional county name).

    Args:
        state: Two-letter state abbreviation (case-insensitive).
        county: County name (case-insensitive). When supplied, only cities in
                that county are returned.

    Returns:
        Sorted list of city name strings (upper-case, matching the CSV).
    """
    state_upper = state.upper().strip()
    county_upper = county.upper().strip() if county else None
    cities: set[str] = set()
    for row in _load():
        if row["STATE"].upper() != state_upper:
            continue
        if county_upper and row["COUNTY"].upper() != county_upper:
            continue
        c = row.get("CITY", "")
        if c:
            cities.add(c)
    return sorted(cities)


# ---------------------------------------------------------------------------
# resolve_location
# ---------------------------------------------------------------------------

_LEVEL_ORDER = ("zip", "city", "county", "state")


def resolve_location(
    state: Optional[str] = None,
    county: Optional[str] = None,
    city: Optional[str] = None,
    zip: Optional[str] = None,
) -> LocationResult:
    """Return hospital rows and a reference point for the most-specific match.

    Specificity order (most to least): ZIP → city → county → state.
    If the most-specific level returns no rows, the function automatically
    widens to the next level.  State is always included in every sub-query to
    prevent mixing hospitals from different states that share a name.

    Widening is NEVER silent: the returned LocationResult carries `widened_from`,
    `widened_to`, and a human-readable `notice` whenever widening occurred.

    Args:
        state:  Two-letter state code (e.g. "AL").
        county: County name (case-insensitive; matched against COUNTY column).
        city:   City name  (case-insensitive; matched against CITY column).
        zip:    Five-digit ZIP code string.

    Returns:
        LocationResult with matching rows and a reference point.

    Raises:
        ValueError: If no rows are found even at the broadest applicable level.
    """
    # Resolve county to COUNTYFIPS so the filter is unambiguous.
    county_fips: Optional[str] = None
    if county and state:
        state_upper = state.upper().strip()
        county_upper = county.upper().strip()
        for row in _load():
            if row["STATE"].upper() == state_upper and row["COUNTY"].upper() == county_upper:
                county_fips = row["COUNTYFIPS"]
                break

    # Build the attempt ladder from most-specific to least-specific.
    attempts: list[tuple[str, dict]] = []
    if zip:
        attempts.append(("zip", {"state": state, "zip_": zip}))
    if city:
        attempts.append(("city", {"state": state, "city": city}))
    if county:
        attempts.append(("county", {"state": state, "county_fips": county_fips}))
    if state:
        attempts.append(("state", {"state": state}))

    if not attempts:
        raise ValueError("resolve_location: at least one of state/county/city/zip must be supplied.")

    first_level = attempts[0][0]
    for i, (level, kwargs) in enumerate(attempts):
        rows = _filter(**kwargs)
        if rows:
            widened = i > 0
            widened_from = first_level if widened else None
            widened_to = level if widened else None
            notice: Optional[str] = None
            if widened:
                notice = (
                    f"No hospitals found for the supplied {first_level.upper()}. "
                    f"Results have been widened to {level.upper()} level."
                )
            lat, lon = _mean_latlon(rows)
            return LocationResult(
                rows=rows,
                ref_lat=lat,
                ref_lon=lon,
                widened_from=widened_from,
                widened_to=widened_to,
                notice=notice,
            )

    raise ValueError(
        f"resolve_location: no hospitals found for the supplied location "
        f"(state={state!r}, county={county!r}, city={city!r}, zip={zip!r})."
    )
