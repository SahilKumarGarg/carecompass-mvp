"""Benchmark test: runs all scenarios through the full offline workflow.

Metrics reported:
  - Emergency recall     (must be 100% — hard test failure if any missed)
  - Urgent recall        (must be 100% — hard test failure if any under-triaged)
  - Under-triage count   (emergency/urgent expected but lower tier returned — hard fail)
  - Over-triage count    (routine expected but higher tier returned — allowed, printed)
  - Tier agreement       (% of all scenarios where actual_tier == expected_tier)
  - Service-type agreement
  - Grounding failures

Under-triage: actual tier is strictly LOWER than expected tier.
  emergency expected → urgent or routine returned = under-triage
  urgent expected    → routine returned           = under-triage

Over-triage: actual tier is strictly HIGHER than expected tier.
  routine expected   → urgent or emergency        = over-triage  (allowed, just reported)

Run with:  pytest tests/test_benchmark.py -v -s
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

# Ensure offline mode for the entire benchmark.
for _k in ("WATSONX_API_KEY", "WATSONX_URL", "WATSONX_PROJECT_ID"):
    os.environ.pop(_k, None)

from src.orchestrator import SessionResult, run_session  # noqa: E402

_SCENARIOS_PATH = Path(__file__).parent.parent / "data" / "scenarios.json"

# Tier severity order for under/over-triage comparison.
_TIER_RANK = {"routine": 0, "urgent": 1, "emergency": 2}


# ---------------------------------------------------------------------------
# Load scenarios once
# ---------------------------------------------------------------------------

with _SCENARIOS_PATH.open(encoding="utf-8") as _fh:
    _ALL_SCENARIOS: list[dict] = json.load(_fh)["scenarios"]


# ---------------------------------------------------------------------------
# Per-scenario result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ScenarioOutcome:
    scenario_id: str
    label: str
    expected_tier: str
    actual_tier: str
    expected_service_type: Optional[str]
    actual_service_type: Optional[str]
    tier_match: bool
    under_triage: bool   # actual tier is strictly lower than expected
    over_triage: bool    # actual tier is strictly higher than expected (allowed)
    service_type_match: Optional[bool]
    grounding_failure: bool
    result: SessionResult


# ---------------------------------------------------------------------------
# Helper: derive top service type from SessionResult
# ---------------------------------------------------------------------------

def _top_service_type(result: SessionResult) -> Optional[str]:
    """Return the type of the top (rank-1) non-fallback service, or None."""
    top = [r for r in result.ranked if not r.is_fallback]
    return top[0].service.type if top else None


# ---------------------------------------------------------------------------
# Run all scenarios
# ---------------------------------------------------------------------------

def _run_all_scenarios() -> list[ScenarioOutcome]:
    outcomes: list[ScenarioOutcome] = []
    for scn in _ALL_SCENARIOS:
        result = run_session(scn["user_text"], scn["area"])
        actual_type = _top_service_type(result)
        expected_type: Optional[str] = scn["expected_service_type"]

        exp_rank = _TIER_RANK[scn["expected_tier"]]
        act_rank = _TIER_RANK[result.tier]

        tier_match = result.tier == scn["expected_tier"]
        under_triage = act_rank < exp_rank
        over_triage  = act_rank > exp_rank

        if expected_type is not None and actual_type is not None:
            type_match: Optional[bool] = actual_type == expected_type
        elif expected_type is None:
            type_match = None
        else:
            type_match = False

        outcomes.append(ScenarioOutcome(
            scenario_id=scn["id"],
            label=scn["label"],
            expected_tier=scn["expected_tier"],
            actual_tier=result.tier,
            expected_service_type=expected_type,
            actual_service_type=actual_type,
            tier_match=tier_match,
            under_triage=under_triage,
            over_triage=over_triage,
            service_type_match=type_match,
            grounding_failure=result.needs_human_review,
            result=result,
        ))
    return outcomes


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def _print_summary(outcomes: list[ScenarioOutcome]) -> dict:
    """Print a benchmark summary table and return a metrics dict."""
    emergency = [o for o in outcomes if o.expected_tier == "emergency"]
    urgent    = [o for o in outcomes if o.expected_tier == "urgent"]
    all_with_type = [o for o in outcomes if o.service_type_match is not None]

    emergency_correct = sum(1 for o in emergency if o.tier_match)
    emergency_recall  = emergency_correct / len(emergency) if emergency else 1.0

    urgent_correct = sum(1 for o in urgent if not o.under_triage)
    urgent_recall  = urgent_correct / len(urgent) if urgent else 1.0

    under_triage_list = [o for o in outcomes if o.under_triage]
    over_triage_list  = [o for o in outcomes if o.over_triage]

    tier_correct   = sum(1 for o in outcomes if o.tier_match)
    tier_agreement = tier_correct / len(outcomes)

    type_correct   = sum(1 for o in all_with_type if o.service_type_match)
    type_agreement = type_correct / len(all_with_type) if all_with_type else 1.0

    grounding_failures = [o for o in outcomes if o.grounding_failure]

    # ── Print table ──────────────────────────────────────────────────────
    col = "{:<10} {:<32} {:<10} {:<10} {:<14} {:<14} {}"
    header = col.format(
        "ID", "Label", "Exp tier", "Act tier",
        "Exp svc type", "Act svc type", "Notes"
    )
    sep = "-" * len(header)

    print("\n" + sep)
    print("CareCompass Offline Benchmark")
    print(sep)
    print(header)
    print(sep)

    for o in outcomes:
        if o.under_triage:
            tier_flag = "UNDER-TRIAGE"
        elif o.over_triage:
            tier_flag = "over-triage"
        elif o.tier_match:
            tier_flag = "OK"
        else:
            tier_flag = "FAIL TIER"

        if o.service_type_match is False:
            type_flag = "FAIL TYPE"
        elif o.service_type_match is None:
            type_flag = "-"
        else:
            type_flag = "OK"

        gnd_flag = " GROUNDING" if o.grounding_failure else ""
        notes = f"{tier_flag} | {type_flag}{gnd_flag}"
        print(col.format(
            o.scenario_id,
            o.label[:31],
            o.expected_tier[:9],
            o.actual_tier[:9],
            str(o.expected_service_type or "-")[:13],
            str(o.actual_service_type or "-")[:13],
            notes,
        ))

    print(sep)
    print(f"Emergency recall:       {emergency_correct}/{len(emergency)} "
          f"({emergency_recall * 100:.0f}%)")
    print(f"Urgent recall:          {urgent_correct}/{len(urgent)} "
          f"({urgent_recall * 100:.0f}%)  [no under-triage allowed]")
    print(f"Under-triage:           {len(under_triage_list)}/{len(outcomes)}  "
          f"[MUST BE 0 — test fails if non-zero]")
    print(f"Over-triage (allowed):  {len(over_triage_list)}/{len(outcomes)}  "
          f"[routine raised to higher tier]")
    print(f"Tier agreement:         {tier_correct}/{len(outcomes)} "
          f"({tier_agreement * 100:.0f}%)")
    print(f"Service-type agreement: {type_correct}/{len(all_with_type)} "
          f"({type_agreement * 100:.0f}%)  [assessed: {len(all_with_type)} scenarios]")
    print(f"Grounding failures:     {len(grounding_failures)}/{len(outcomes)}")
    if under_triage_list:
        print("  UNDER-TRIAGE FAILURES:")
        for o in under_triage_list:
            print(f"    -> {o.scenario_id} ({o.label}): "
                  f"expected {o.expected_tier}, got {o.actual_tier}")
    if over_triage_list:
        print("  Over-triage (info):")
        for o in over_triage_list:
            print(f"    -> {o.scenario_id} ({o.label}): "
                  f"expected {o.expected_tier}, got {o.actual_tier}")
    if grounding_failures:
        print("  Grounding failures:")
        for o in grounding_failures:
            print(f"    -> {o.scenario_id} {o.label}")
    print(sep + "\n")

    return {
        "emergency_recall": emergency_recall,
        "urgent_recall": urgent_recall,
        "under_triage_count": len(under_triage_list),
        "over_triage_count": len(over_triage_list),
        "tier_agreement": tier_agreement,
        "type_agreement": type_agreement,
        "grounding_failure_count": len(grounding_failures),
        "emergency_correct": emergency_correct,
        "emergency_total": len(emergency),
        "urgent_correct": urgent_correct,
        "urgent_total": len(urgent),
        "tier_correct": tier_correct,
        "tier_total": len(outcomes),
        "type_correct": type_correct,
        "type_total": len(all_with_type),
    }


# ---------------------------------------------------------------------------
# Compute outcomes once at module load so all tests share the same run
# ---------------------------------------------------------------------------

_OUTCOMES: list[ScenarioOutcome] = _run_all_scenarios()
_METRICS: dict = _print_summary(_OUTCOMES)


# ---------------------------------------------------------------------------
# HARD REQUIREMENT: no under-triage
# ---------------------------------------------------------------------------

class TestUnderTriage:
    """Any scenario classified at a lower tier than expected is a hard test failure."""

    def test_no_under_triage(self) -> None:
        """Zero under-triage across all scenarios — HARD FAIL if violated."""
        under = [o for o in _OUTCOMES if o.under_triage]
        assert under == [], (
            f"{len(under)} scenario(s) were under-triaged:\n"
            + "\n".join(
                f"  {o.scenario_id} ({o.label}): "
                f"expected {o.expected_tier!r}, got {o.actual_tier!r}"
                for o in under
            )
        )

    @pytest.mark.parametrize(
        "outcome",
        [o for o in _OUTCOMES if o.expected_tier in {"emergency", "urgent"}],
        ids=[o.scenario_id for o in _OUTCOMES if o.expected_tier in {"emergency", "urgent"}],
    )
    def test_each_high_priority_scenario_not_under_triaged(
        self, outcome: ScenarioOutcome
    ) -> None:
        """Each emergency/urgent scenario must not be classified at a lower tier."""
        assert not outcome.under_triage, (
            f"{outcome.scenario_id} ({outcome.label}): "
            f"expected {outcome.expected_tier!r}, got {outcome.actual_tier!r}"
        )


# ---------------------------------------------------------------------------
# HARD REQUIREMENT: emergency recall = 100%
# ---------------------------------------------------------------------------

class TestEmergencyRecall:
    """Emergency scenarios must NEVER be missed."""

    def test_emergency_recall_is_100_percent(self) -> None:
        missed = [
            o for o in _OUTCOMES
            if o.expected_tier == "emergency" and not o.tier_match
        ]
        assert missed == [], (
            f"{len(missed)} emergency scenario(s) were NOT detected as emergency:\n"
            + "\n".join(f"  {o.scenario_id} ({o.label}): got {o.actual_tier!r}"
                        for o in missed)
        )

    @pytest.mark.parametrize(
        "outcome",
        [o for o in _OUTCOMES if o.expected_tier == "emergency"],
        ids=[o.scenario_id for o in _OUTCOMES if o.expected_tier == "emergency"],
    )
    def test_each_emergency_scenario_detected(self, outcome: ScenarioOutcome) -> None:
        assert outcome.tier_match, (
            f"{outcome.scenario_id} ({outcome.label}): "
            f"expected emergency, got {outcome.actual_tier!r}"
        )

    @pytest.mark.parametrize(
        "outcome",
        [o for o in _OUTCOMES if o.expected_tier == "emergency"],
        ids=[o.scenario_id for o in _OUTCOMES if o.expected_tier == "emergency"],
    )
    def test_emergency_skips_service_search(self, outcome: ScenarioOutcome) -> None:
        assert outcome.result.candidates == [], (
            f"{outcome.scenario_id}: expected no candidates on emergency path"
        )
        assert outcome.result.ranked == [], (
            f"{outcome.scenario_id}: expected no ranked results on emergency path"
        )


# ---------------------------------------------------------------------------
# Over-triage (informational — does NOT fail the test)
# ---------------------------------------------------------------------------

class TestOverTriage:
    def test_over_triage_count_reported(self) -> None:
        """Over-triage is allowed; this test always passes and documents the count."""
        count = _METRICS["over_triage_count"]
        # Always passes — over-triage is acceptable (safe-side).
        assert isinstance(count, int)

    def test_no_routine_scenario_returns_emergency(self) -> None:
        """A routine scenario must never jump all the way to emergency tier."""
        false_emergencies = [
            o for o in _OUTCOMES
            if o.expected_tier == "routine" and o.actual_tier == "emergency"
        ]
        assert false_emergencies == [], (
            "Routine scenario(s) jumped to emergency: "
            + ", ".join(o.scenario_id for o in false_emergencies)
        )


# ---------------------------------------------------------------------------
# Metrics / structural tests
# ---------------------------------------------------------------------------

class TestBenchmarkMetrics:
    def test_metrics_dict_has_expected_keys(self) -> None:
        required = {
            "emergency_recall", "urgent_recall", "under_triage_count",
            "over_triage_count", "tier_agreement", "type_agreement",
            "grounding_failure_count", "emergency_total", "urgent_total",
        }
        assert required.issubset(_METRICS.keys())

    def test_emergency_total_is_four(self) -> None:
        assert _METRICS["emergency_total"] == 4

    def test_urgent_total_is_nine(self) -> None:
        """Original 4 urgent scenarios + 5 new ones = 9."""
        assert _METRICS["urgent_total"] == 9

    def test_tier_total_is_seventeen(self) -> None:
        """4 emergency + 9 urgent + 4 routine = 17."""
        assert _METRICS["tier_total"] == 17

    def test_all_scenarios_ran(self) -> None:
        assert len(_OUTCOMES) == len(_ALL_SCENARIOS)

    def test_each_outcome_has_session_result(self) -> None:
        for o in _OUTCOMES:
            assert isinstance(o.result, SessionResult)

    def test_grounding_failures_reported_as_int(self) -> None:
        assert isinstance(_METRICS["grounding_failure_count"], int)

    def test_tier_agreement_is_float(self) -> None:
        assert isinstance(_METRICS["tier_agreement"], float)

    def test_type_agreement_is_float(self) -> None:
        assert isinstance(_METRICS["type_agreement"], float)

    def test_under_triage_count_is_zero(self) -> None:
        """Mirrors the hard test above — under-triage count must be zero."""
        assert _METRICS["under_triage_count"] == 0

    def test_non_emergency_scenarios_have_candidates_or_review(self) -> None:
        # Exclude scenarios that were over-triaged to emergency — those correctly
        # follow the emergency path (no candidates by design).
        non_em = [
            o for o in _OUTCOMES
            if o.expected_tier != "emergency" and o.actual_tier != "emergency"
        ]
        for o in non_em:
            has_ranked = len(o.result.ranked) > 0
            assert has_ranked or o.grounding_failure, (
                f"{o.scenario_id}: no ranked results and no human-review flag"
            )


# ---------------------------------------------------------------------------
# Summary print captured in test output
# ---------------------------------------------------------------------------

class TestSummaryOutput:
    def test_summary_printed(self, capsys: pytest.CaptureFixture) -> None:
        _print_summary(_OUTCOMES)
        captured = capsys.readouterr()
        assert "Emergency recall" in captured.out
        assert "Urgent recall" in captured.out
        assert "Under-triage" in captured.out
        assert "Over-triage" in captured.out
        assert "Grounding failures" in captured.out
