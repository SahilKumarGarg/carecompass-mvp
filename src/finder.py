"""Finder module: map a case to service types and return filtered candidates.

All service facts come exclusively from data/services.json.
No LLM calls are made in this module.
"""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path
from typing import Optional

from src.intake import CaseResult
from src.safety import RuleHit

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DATA = Path(__file__).parent.parent / "data"
_SERVICES_PATH = _DATA / "services.json"
_CARE_LEVELS_PATH = _DATA / "care_levels.json"
_AREA_CENTRES_PATH = _DATA / "area_centres.json"
_RED_FLAGS_PATH = _DATA / "red_flags.json"

# Travel speed used for time estimate (labelled as estimate throughout).
_TRAVEL_SPEED_KMH = 25.0


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ServiceCandidate:
    """A service record enriched with distance and travel-time estimates."""

    id: str
    name: str
    type: str
    area: str
    services_offered: list[str]
    hours: str
    open_24h: bool
    languages: list[str]
    cost_note: str
    phone: str
    last_verified: str
    distance_km: Optional[float]        # None for telemedicine
    travel_min_estimate: Optional[float]  # None for telemedicine; labelled estimate
    fit_score: int                      # 0–3: how well type matches preferred types list


# ---------------------------------------------------------------------------
# Loaders (cached at module level after first call)
# ---------------------------------------------------------------------------

_services_cache: list[dict] | None = None
_care_levels_cache: list[dict] | None = None
_area_centres_cache: dict[str, dict] | None = None
_red_flags_cache: dict[str, dict] | None = None


def _load_services() -> list[dict]:
    global _services_cache
    if _services_cache is None:
        with _SERVICES_PATH.open(encoding="utf-8") as fh:
            _services_cache = json.load(fh)["services"]
    return _services_cache


def _load_care_levels() -> list[dict]:
    global _care_levels_cache
    if _care_levels_cache is None:
        with _CARE_LEVELS_PATH.open(encoding="utf-8") as fh:
            _care_levels_cache = json.load(fh)["care_levels"]
    return _care_levels_cache


def _load_area_centres() -> dict[str, dict]:
    global _area_centres_cache
    if _area_centres_cache is None:
        with _AREA_CENTRES_PATH.open(encoding="utf-8") as fh:
            _area_centres_cache = json.load(fh)["area_centres"]
    return _area_centres_cache


def _load_red_flags_index() -> dict[str, dict]:
    """Return a dict keyed by rule ID containing the full rule record."""
    global _red_flags_cache
    if _red_flags_cache is None:
        with _RED_FLAGS_PATH.open(encoding="utf-8") as fh:
            rules = json.load(fh)["red_flag_categories"]
        _red_flags_cache = {r["id"]: r for r in rules}
    return _red_flags_cache


# ---------------------------------------------------------------------------
# Haversine distance
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in km between two lat/lon points."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Care-level mapping
# ---------------------------------------------------------------------------

def map_to_service_types(
    case: CaseResult,
    tier: str,
    safety_hits: list[RuleHit] | None = None,
) -> list[str]:
    """Return an ordered list of preferred service types for this case.

    When safety_hits are provided and an urgent rule carries a service_type hint,
    that type is promoted to the front of the list ahead of keyword-based matches.
    Emergency tier always returns ["hospital_emergency"] only.
    Falls back to ["primary_care", "telemedicine"] if no keyword match.

    Args:
        case: Structured case from intake.
        tier: Safety tier ("emergency" | "urgent" | "routine").
        safety_hits: Optional list of RuleHit from check_red_flags; used to
            pull the service_type hint from the matched rule.

    Returns:
        Ordered list of service type strings.
    """
    if tier == "emergency":
        return ["hospital_emergency"]

    # W8-1: derive a rule-level service_type hint from the highest-priority hit.
    rule_type: str | None = None
    if safety_hits:
        rf_index = _load_red_flags_index()
        for hit in safety_hits:
            rule = rf_index.get(hit.rule_id, {})
            hint = rule.get("service_type")
            if hint:
                rule_type = hint
                break  # highest-priority hit wins

    care_levels = _load_care_levels()
    concern_lower = (case.concern + " " + case.who).lower()

    matched: list[str] = []
    for cl in care_levels:
        for kw in cl["keywords"]:
            if kw.lower() in concern_lower:
                for t in cl["preferred_types"]:
                    if t not in matched:
                        matched.append(t)
                break  # one keyword match per care level is enough

    # Urgent tier: put the rule-derived type at the front if present,
    # then hospital_emergency, then keyword matches.
    if tier == "urgent":
        front: list[str] = []
        if rule_type and rule_type not in front:
            front.append(rule_type)
        if "hospital_emergency" not in front:
            front.append("hospital_emergency")
        for t in matched:
            if t not in front:
                front.append(t)
        matched = front

    if not matched:
        if tier == "urgent":
            return ["hospital_emergency", "primary_care", "telemedicine"]
        return ["primary_care", "telemedicine", "pharmacy"]

    return matched


# ---------------------------------------------------------------------------
# Filtering and enrichment
# ---------------------------------------------------------------------------

def _fit_score(service_type: str, preferred_types: list[str]) -> int:
    """Return 3 for first preference, 2 for second, 1 for third, 0 otherwise."""
    try:
        pos = preferred_types.index(service_type)
        return max(0, 3 - pos)
    except ValueError:
        return 0


def find_services(
    case: CaseResult,
    tier: str,
    safety_hits: list[RuleHit] | None = None,
) -> list[ServiceCandidate]:
    """Find services matching the case area and preferred service types.

    Filters data/services.json by area, then by whether the service type
    appears in the preferred-types list.  Adds distance_km and
    travel_min_estimate (labelled as estimates) using haversine from the
    area centre.

    Args:
        case: Structured case from intake.
        tier: Safety tier ("emergency" | "urgent" | "routine").
        safety_hits: Optional list of RuleHit; used to pick service type
            from the matched rule (W8-1).

    Returns:
        List of ServiceCandidate, unsorted (sorting is done by advisor).
    """
    preferred_types = map_to_service_types(case, tier, safety_hits)
    services = _load_services()
    centres = _load_area_centres()

    area_centre = centres.get(case.area)

    candidates: list[ServiceCandidate] = []
    for svc in services:
        if svc["area"].lower() != case.area.lower():
            continue
        fit = _fit_score(svc["type"], preferred_types)
        if fit == 0:
            continue  # type not in preferred list at all

        # Distance and travel time (telemedicine has no location).
        if svc["lat"] is not None and svc["lon"] is not None and area_centre:
            dist = haversine_km(
                area_centre["lat"], area_centre["lon"],
                svc["lat"], svc["lon"],
            )
            travel = round((dist / _TRAVEL_SPEED_KMH) * 60, 1)
        else:
            dist = None
            travel = None

        candidates.append(ServiceCandidate(
            id=svc["id"],
            name=svc["name"],
            type=svc["type"],
            area=svc["area"],
            services_offered=svc["services_offered"],
            hours=svc["hours"],
            open_24h=svc["open_24h"],
            languages=svc["languages"],
            cost_note=svc["cost_note"],
            phone=svc["phone"],
            last_verified=svc["last_verified"],
            distance_km=round(dist, 2) if dist is not None else None,
            travel_min_estimate=travel,
            fit_score=fit,
        ))

    return candidates
