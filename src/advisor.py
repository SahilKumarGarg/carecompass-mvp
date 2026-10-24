"""Advisor module: rank service candidates and build recommendations.

Scores on fit, distance, open status and data freshness.
Urgent cases weight distance and open status more heavily.
Reasons are built from record fields only; optionally reworded by an LLM.
"""

from __future__ import annotations

import dataclasses
import datetime
import os
from typing import Optional

from src.finder import ServiceCandidate

# ---------------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------------

# Weights for (fit, distance, open_now, freshness).
_WEIGHTS_ROUTINE: tuple[float, float, float, float] = (0.40, 0.20, 0.20, 0.20)
_WEIGHTS_URGENT:  tuple[float, float, float, float] = (0.25, 0.35, 0.30, 0.10)

_MAX_DISTANCE_KM = 10.0   # beyond this, distance score is 0
_FRESHNESS_DAYS = 90      # data older than this scores 0 on freshness
_STALE_DAYS = 60          # threshold: show "call ahead" warning + extra ranking penalty


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class RankedResult:
    """A ranked recommendation."""

    rank: int                         # 1 = top recommendation, 4 = fallback
    service: ServiceCandidate
    score: float                      # composite 0.0–1.0
    reason: str                       # human-readable reason (from record fields)
    is_fallback: bool                 # True only for the 4th item


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _score_fit(candidate: ServiceCandidate) -> float:
    """Normalise fit_score (0–3) to 0.0–1.0."""
    return candidate.fit_score / 3.0


def _score_distance(candidate: ServiceCandidate) -> float:
    """Closer = higher score.  Telemedicine is treated as distance 0 (always reachable)."""
    if candidate.distance_km is None:
        return 1.0  # telemedicine: no travel required
    clamped = min(candidate.distance_km, _MAX_DISTANCE_KM)
    return 1.0 - (clamped / _MAX_DISTANCE_KM)


def _score_open(candidate: ServiceCandidate) -> float:
    """open_24h services score 1.0; others score 0.5 (hours not parsed)."""
    return 1.0 if candidate.open_24h else 0.5


def _days_since_verified(candidate: ServiceCandidate) -> int | None:
    """Return the number of days since last_verified, or None if unparseable."""
    try:
        verified = datetime.date.fromisoformat(candidate.last_verified)
        age = (datetime.date.today() - verified).days
        return max(0, age)
    except (ValueError, TypeError):
        return None


def _is_stale(candidate: ServiceCandidate) -> bool:
    """Return True when last_verified is more than _STALE_DAYS days ago."""
    age = _days_since_verified(candidate)
    return age is None or age > _STALE_DAYS


def _score_freshness(candidate: ServiceCandidate) -> float:
    """More recently verified = higher score.  Scores 0 if > _FRESHNESS_DAYS old.

    An extra penalty of 0.3 is applied when data is stale (> _STALE_DAYS days),
    so stale services rank meaningfully lower than fresh ones.
    """
    age = _days_since_verified(candidate)
    if age is None:
        return 0.0
    base = max(0.0, 1.0 - age / _FRESHNESS_DAYS)
    if age > _STALE_DAYS:
        base = max(0.0, base - 0.3)
    return base


def _composite_score(candidate: ServiceCandidate, tier: str) -> float:
    """Return a weighted composite score for the candidate."""
    weights = _WEIGHTS_URGENT if tier == "urgent" else _WEIGHTS_ROUTINE
    w_fit, w_dist, w_open, w_fresh = weights
    return (
        w_fit   * _score_fit(candidate)
        + w_dist  * _score_distance(candidate)
        + w_open  * _score_open(candidate)
        + w_fresh * _score_freshness(candidate)
    )


# ---------------------------------------------------------------------------
# Reason builder — facts from record fields only
# ---------------------------------------------------------------------------

def _build_reason_template(candidate: ServiceCandidate) -> str:
    """Build a reason string using only fields from the service record."""
    parts: list[str] = []

    if candidate.distance_km is not None:
        parts.append(
            f"{candidate.name} is approximately {candidate.distance_km:.1f} km away "
            f"(estimated {candidate.travel_min_estimate:.0f} min travel at 25 km/h — estimate only)."
        )
    else:
        parts.append(f"{candidate.name} is available online with no travel needed.")

    if candidate.open_24h:
        parts.append("It is open 24 hours.")
    else:
        parts.append(f"Hours: {candidate.hours}.")

    offered = ", ".join(candidate.services_offered[:3])
    parts.append(f"Services include: {offered}.")

    age = _days_since_verified(candidate)
    if age is not None and age > _STALE_DAYS:
        parts.append(
            f"Last verified {age} days ago — call ahead to confirm details are still current."
        )
    else:
        parts.append(f"Data last verified: {candidate.last_verified}.")
    parts.append(f"Phone: {candidate.phone}.")

    return " ".join(parts)


def _reword_reason_llm(reason: str, candidate: ServiceCandidate) -> str:
    """Attempt to reword the reason using watsonx.ai.  Falls back to template on any error."""
    try:
        from ibm_watsonx_ai import APIClient, Credentials  # type: ignore
        from ibm_watsonx_ai.foundation_models import ModelInference  # type: ignore

        api_key = os.environ["WATSONX_API_KEY"]
        url = os.environ["WATSONX_URL"]
        project_id = os.environ["WATSONX_PROJECT_ID"]

        credentials = Credentials(url=url, api_key=api_key)
        client = APIClient(credentials)
        model = ModelInference(
            model_id="ibm/granite-13b-instruct-v2",
            api_client=client,
            project_id=project_id,
        )

        prompt = (
            "Reword the following service recommendation in plain, friendly English. "
            "Do not add any facts not present in the text. "
            "Do not diagnose or suggest treatments. "
            "Keep it under 60 words.\n\n"
            f"Original: {reason}\n\nReworded:"
        )
        rewarded = model.generate_text(prompt=prompt, guardrails=False).strip()
        return rewarded if rewarded else reason
    except Exception:  # noqa: BLE001
        return reason


def _watsonx_available() -> bool:
    """Return True when all three watsonx.ai env vars are set and non-empty."""
    return all(
        os.environ.get(k, "").strip()
        for k in ("WATSONX_API_KEY", "WATSONX_URL", "WATSONX_PROJECT_ID")
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def rank(candidates: list[ServiceCandidate], tier: str) -> list[RankedResult]:
    """Rank candidates and return top 3 + a fallback (4 results total).

    Scores each candidate on fit (type preference), distance, open status and
    data freshness.  Urgent cases weight distance and open status more heavily.
    Reasons are built only from record fields, then optionally reworded by LLM.

    Args:
        candidates: ServiceCandidate list from finder.find_services.
        tier: Safety tier — "emergency" | "urgent" | "routine".

    Returns:
        Up to 4 RankedResult items: positions 1–3 are recommendations,
        position 4 (is_fallback=True) is the fallback.  May be fewer than 4
        if fewer candidates are provided.
    """
    if not candidates:
        return []

    scored = sorted(
        candidates,
        key=lambda c: _composite_score(c, tier),
        reverse=True,
    )

    use_llm = _watsonx_available()
    results: list[RankedResult] = []
    for i, candidate in enumerate(scored[:4]):
        is_fallback = i == 3
        rank_pos = i + 1
        reason = _build_reason_template(candidate)
        if use_llm:
            reason = _reword_reason_llm(reason, candidate)
        results.append(RankedResult(
            rank=rank_pos,
            service=candidate,
            score=round(_composite_score(candidate, tier), 4),
            reason=reason,
            is_fallback=is_fallback,
        ))

    return results
