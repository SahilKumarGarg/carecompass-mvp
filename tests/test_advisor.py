"""Tests for src/advisor.py."""

from __future__ import annotations

import os
import datetime

import pytest

# Ensure no LLM calls during tests.
for _k in ("WATSONX_API_KEY", "WATSONX_URL", "WATSONX_PROJECT_ID"):
    os.environ.pop(_k, None)

from src.advisor import (  # noqa: E402
    RankedResult,
    _build_reason_template,
    _composite_score,
    _score_distance,
    _score_fit,
    _score_freshness,
    _score_open,
    rank,
)
from src.finder import ServiceCandidate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _candidate(
    id: str = "SVC001",
    name: str = "Test Service",
    svc_type: str = "primary_care",
    open_24h: bool = False,
    distance_km: float | None = 1.0,
    fit_score: int = 3,
    last_verified: str | None = None,
) -> ServiceCandidate:
    if last_verified is None:
        last_verified = datetime.date.today().isoformat()
    travel = round((distance_km / 25.0) * 60, 1) if distance_km is not None else None
    return ServiceCandidate(
        id=id,
        name=name,
        type=svc_type,
        area="Greenfield",
        services_offered=["GP appointments", "minor illness advice"],
        hours="Mon-Fri 08:00-18:00",
        open_24h=open_24h,
        languages=["English"],
        cost_note="NHS",
        phone="020-7000-0001",
        last_verified=last_verified,
        distance_km=distance_km,
        travel_min_estimate=travel,
        fit_score=fit_score,
    )


# ---------------------------------------------------------------------------
# Individual scoring functions
# ---------------------------------------------------------------------------

class TestScoreFit:
    def test_fit_3_scores_1(self) -> None:
        assert _score_fit(_candidate(fit_score=3)) == pytest.approx(1.0)

    def test_fit_0_scores_0(self) -> None:
        assert _score_fit(_candidate(fit_score=0)) == pytest.approx(0.0)

    def test_fit_1_scores_one_third(self) -> None:
        assert _score_fit(_candidate(fit_score=1)) == pytest.approx(1 / 3)


class TestScoreDistance:
    def test_zero_distance_scores_1(self) -> None:
        assert _score_distance(_candidate(distance_km=0.0)) == pytest.approx(1.0)

    def test_telemedicine_no_distance_scores_1(self) -> None:
        assert _score_distance(_candidate(distance_km=None)) == pytest.approx(1.0)

    def test_max_distance_scores_0(self) -> None:
        assert _score_distance(_candidate(distance_km=10.0)) == pytest.approx(0.0)

    def test_beyond_max_clamped_to_0(self) -> None:
        assert _score_distance(_candidate(distance_km=20.0)) == pytest.approx(0.0)

    def test_midpoint_scores_half(self) -> None:
        assert _score_distance(_candidate(distance_km=5.0)) == pytest.approx(0.5)


class TestScoreOpen:
    def test_open_24h_scores_1(self) -> None:
        assert _score_open(_candidate(open_24h=True)) == pytest.approx(1.0)

    def test_not_open_24h_scores_half(self) -> None:
        assert _score_open(_candidate(open_24h=False)) == pytest.approx(0.5)


class TestScoreFreshness:
    def test_verified_today_scores_1(self) -> None:
        today = datetime.date.today().isoformat()
        assert _score_freshness(_candidate(last_verified=today)) == pytest.approx(1.0)

    def test_old_data_scores_lower(self) -> None:
        old = (datetime.date.today() - datetime.timedelta(days=60)).isoformat()
        score = _score_freshness(_candidate(last_verified=old))
        assert score < 1.0
        assert score > 0.0

    def test_very_old_data_scores_0(self) -> None:
        ancient = "2020-01-01"
        assert _score_freshness(_candidate(last_verified=ancient)) == pytest.approx(0.0)

    def test_invalid_date_scores_0(self) -> None:
        assert _score_freshness(_candidate(last_verified="not-a-date")) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Composite score
# ---------------------------------------------------------------------------

class TestCompositeScore:
    def test_perfect_candidate_scores_high(self) -> None:
        c = _candidate(fit_score=3, open_24h=True, distance_km=0.0)
        score = _composite_score(c, "routine")
        assert score > 0.8

    def test_poor_candidate_scores_low(self) -> None:
        c = _candidate(fit_score=1, open_24h=False, distance_km=9.5,
                       last_verified="2023-01-01")
        score = _composite_score(c, "routine")
        assert score < 0.5

    def test_urgent_weights_distance_more(self) -> None:
        """For urgent tier, a closer service should outscore a farther one more than for routine."""
        near = _candidate(id="A", fit_score=2, open_24h=True, distance_km=0.5)
        far  = _candidate(id="B", fit_score=2, open_24h=True, distance_km=8.0)
        urgent_gap  = _composite_score(near, "urgent") - _composite_score(far, "urgent")
        routine_gap = _composite_score(near, "routine") - _composite_score(far, "routine")
        assert urgent_gap > routine_gap

    def test_score_in_range(self) -> None:
        c = _candidate()
        score = _composite_score(c, "routine")
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# rank()
# ---------------------------------------------------------------------------

class TestRank:
    def test_empty_candidates_returns_empty(self) -> None:
        assert rank([], "routine") == []

    def test_returns_ranked_result_instances(self) -> None:
        results = rank([_candidate()], "routine")
        assert all(isinstance(r, RankedResult) for r in results)

    def test_at_most_four_results(self) -> None:
        candidates = [_candidate(id=f"SVC{i:03d}", name=f"Svc {i}") for i in range(10)]
        results = rank(candidates, "routine")
        assert len(results) <= 4

    def test_four_results_when_four_or_more_candidates(self) -> None:
        candidates = [_candidate(id=f"SVC{i:03d}", name=f"Svc {i}", distance_km=float(i))
                      for i in range(6)]
        results = rank(candidates, "routine")
        assert len(results) == 4

    def test_fourth_result_is_fallback(self) -> None:
        candidates = [_candidate(id=f"SVC{i:03d}", name=f"Svc {i}") for i in range(4)]
        results = rank(candidates, "routine")
        assert results[-1].is_fallback is True

    def test_first_three_not_fallback(self) -> None:
        candidates = [_candidate(id=f"SVC{i:03d}", name=f"Svc {i}") for i in range(4)]
        for r in rank(candidates, "routine")[:3]:
            assert r.is_fallback is False

    def test_ranked_by_score_descending(self) -> None:
        candidates = [
            _candidate(id="SVC001", fit_score=3, open_24h=True,  distance_km=0.5),
            _candidate(id="SVC002", fit_score=1, open_24h=False, distance_km=9.0),
            _candidate(id="SVC003", fit_score=2, open_24h=False, distance_km=4.0),
        ]
        results = rank(candidates, "routine")
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_rank_numbers_sequential(self) -> None:
        candidates = [_candidate(id=f"SVC{i:03d}", name=f"Svc {i}") for i in range(3)]
        results = rank(candidates, "routine")
        assert [r.rank for r in results] == list(range(1, len(results) + 1))

    def test_single_candidate_is_rank_1(self) -> None:
        results = rank([_candidate()], "routine")
        assert results[0].rank == 1
        assert results[0].is_fallback is False

    def test_urgent_promotes_open_24h_service(self) -> None:
        """Under urgent weighting, an open-24h service should outscore a closed one."""
        open_svc   = _candidate(id="A", fit_score=2, open_24h=True,  distance_km=1.0)
        closed_svc = _candidate(id="B", fit_score=2, open_24h=False, distance_km=1.0)
        results = rank([open_svc, closed_svc], "urgent")
        assert results[0].service.id == "A"

    def test_reason_contains_service_name(self) -> None:
        c = _candidate(name="My Test Clinic")
        results = rank([c], "routine")
        assert "My Test Clinic" in results[0].reason

    def test_reason_contains_phone(self) -> None:
        c = _candidate()
        results = rank([c], "routine")
        assert "020-7000-0001" in results[0].reason

    def test_reason_contains_last_verified(self) -> None:
        c = _candidate()
        results = rank([c], "routine")
        assert c.last_verified in results[0].reason

    def test_score_field_is_float(self) -> None:
        results = rank([_candidate()], "routine")
        assert isinstance(results[0].score, float)


# ---------------------------------------------------------------------------
# _build_reason_template
# ---------------------------------------------------------------------------

class TestBuildReasonTemplate:
    def test_contains_distance_for_physical(self) -> None:
        c = _candidate(distance_km=2.5)
        r = _build_reason_template(c)
        assert "2.5" in r

    def test_contains_online_for_telemedicine(self) -> None:
        c = _candidate(distance_km=None)
        r = _build_reason_template(c)
        assert "online" in r.lower()

    def test_contains_24h_note_when_open_24h(self) -> None:
        c = _candidate(open_24h=True)
        r = _build_reason_template(c)
        assert "24" in r

    def test_estimate_label_present(self) -> None:
        c = _candidate(distance_km=1.5)
        r = _build_reason_template(c)
        assert "estimate" in r.lower()
