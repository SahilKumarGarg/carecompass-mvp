"""Benchmark test: runs all 12 scenarios through the full offline workflow.

Metrics reported:
  - Emergency recall        (must be 100% — test fails if any missed)
  - Tier agreement          (% of all scenarios where actual_tier == expected_tier)
  - Service-type agreement  (% of non-emergency scenarios where top service type matches)
  - Grounding failures      (scenarios where needs_human_review is True)

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
    actual_service_type: Optional[str]   # type of top non-fallback ranked result
    tier_match: bool
    service_type_match: Optional[bool]   # None when expected_service_type is null
    grounding_failure: bool              # needs_human_review is True
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

        tier_match = result.tier == scn["expected_tier"]
        if expected_type is not None and actual_type is not None:
            type_match: Optional[bool] = actual_type == expected_type
        elif expected_type is None:
            type_match = None   # not assessed (e.g. emergency scenarios have null expected type)
        else:
            type_match = False  # expected type exists but nothing was ranked

        outcomes.append(ScenarioOutcome(
            scenario_id=scn["id"],
            label=scn["label"],
            expected_tier=scn["expected_tier"],
            actual_tier=result.tier,
            expected_service_type=expected_type,
            actual_service_type=actual_type,
            tier_match=tier_match,
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
    non_emergency = [o for o in outcomes if o.expected_tier != "emergency"]
    all_with_type = [o for o in outcomes if o.service_type_match is not None]

    emergency_correct = sum(1 for o in emergency if o.tier_match)
    emergency_recall = emergency_correct / len(emergency) if emergency else 1.0

    tier_correct = sum(1 for o in outcomes if o.tier_match)
    tier_agreement = tier_correct / len(outcomes)

    type_correct = sum(1 for o in all_with_type if o.service_type_match)
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
        tier_flag = "OK" if o.tier_match else "FAIL TIER"
        type_flag = ""
        if o.service_type_match is False:
            type_flag = "FAIL TYPE"
        elif o.service_type_match is None:
            type_flag = "-"
        else:
            type_flag = "OK"
        gnd_flag = " GROUNDING FAIL" if o.grounding_failure else ""
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
    print(f"Tier agreement:         {tier_correct}/{len(outcomes)} "
          f"({tier_agreement * 100:.0f}%)")
    print(f"Service-type agreement: {type_correct}/{len(all_with_type)} "
          f"({type_agreement * 100:.0f}%)  [assessed: {len(all_with_type)} scenarios]")
    print(f"Grounding failures:     {len(grounding_failures)}/{len(outcomes)}")
    if grounding_failures:
        for o in grounding_failures:
            print(f"  -> {o.scenario_id} {o.label}")
    print(sep + "\n")

    return {
        "emergency_recall": emergency_recall,
        "tier_agreement": tier_agreement,
        "type_agreement": type_agreement,
        "grounding_failure_count": len(grounding_failures),
        "emergency_correct": emergency_correct,
        "emergency_total": len(emergency),
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
# Hard requirement: emergency recall must be 100%
# ---------------------------------------------------------------------------

class TestEmergencyRecall:
    """Emergency scenarios must NEVER be missed. This is a hard test failure."""

    def test_emergency_recall_is_100_percent(self) -> None:
        """All emergency scenarios must return tier='emergency'."""
        missed = [
            o for o in _OUTCOMES
            if o.expected_tier == "emergency" and not o.tier_match
        ]
        assert missed == [], (
            f"{len(missed)} emergency scenario(s) were NOT detected as emergency:\n"
            + "\n".join(f"  {o.scenario_id} ({o.label}): got tier={o.actual_tier!r}"
                        for o in missed)
        )

    @pytest.mark.parametrize(
        "outcome",
        [o for o in _OUTCOMES if o.expected_tier == "emergency"],
        ids=[o.scenario_id for o in _OUTCOMES if o.expected_tier == "emergency"],
    )
    def test_each_emergency_scenario_detected(self, outcome: ScenarioOutcome) -> None:
        """Each emergency scenario individually must return tier='emergency'."""
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
        """Emergency path must produce zero candidates and zero ranked results."""
        assert outcome.result.candidates == [], (
            f"{outcome.scenario_id}: expected no candidates on emergency path"
        )
        assert outcome.result.ranked == [], (
            f"{outcome.scenario_id}: expected no ranked results on emergency path"
        )


# ---------------------------------------------------------------------------
# Metrics summary tests (informational — do not gate on these)
# ---------------------------------------------------------------------------

class TestBenchmarkMetrics:
    def test_metrics_dict_has_expected_keys(self) -> None:
        required = {
            "emergency_recall", "tier_agreement", "type_agreement",
            "grounding_failure_count", "emergency_total",
        }
        assert required.issubset(_METRICS.keys())

    def test_emergency_total_is_four(self) -> None:
        """scenarios.json has exactly 4 emergency scenarios."""
        assert _METRICS["emergency_total"] == 4

    def test_tier_total_is_twelve(self) -> None:
        """scenarios.json has exactly 12 scenarios in total."""
        assert _METRICS["tier_total"] == 12

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

    def test_non_emergency_scenarios_have_candidates_or_review(self) -> None:
        """Non-emergency scenarios should either rank something or hit human review."""
        non_em = [o for o in _OUTCOMES if o.expected_tier != "emergency"]
        for o in non_em:
            has_ranked = len(o.result.ranked) > 0
            assert has_ranked or o.grounding_failure, (
                f"{o.scenario_id}: no ranked results and no human-review flag"
            )


# ---------------------------------------------------------------------------
# Tier-specific checks
# ---------------------------------------------------------------------------

class TestTierCoverage:
    def test_no_routine_scenario_returns_emergency(self) -> None:
        """A routine scenario must never escalate to emergency."""
        false_emergencies = [
            o for o in _OUTCOMES
            if o.expected_tier == "routine" and o.actual_tier == "emergency"
        ]
        assert false_emergencies == [], (
            "Routine scenario(s) incorrectly classified as emergency: "
            + ", ".join(o.scenario_id for o in false_emergencies)
        )

    def test_summary_printed(self, capsys: pytest.CaptureFixture) -> None:
        """Verify the summary was printed to stdout (captured by capsys)."""
        # Re-print to ensure capsys captures it in this test's output.
        _print_summary(_OUTCOMES)
        captured = capsys.readouterr()
        assert "Emergency recall" in captured.out
        assert "Tier agreement" in captured.out
        assert "Grounding failures" in captured.out
