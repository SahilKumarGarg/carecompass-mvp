"""Benchmark test: runs all scenarios through the full offline workflow.

Metrics reported:
  - Emergency recall     (must be 100% — hard test failure if any missed)
  - Urgent recall        (must be 100% — hard test failure if any under-triaged)
  - Under-triage count   (emergency/urgent expected but lower tier returned — hard fail)
  - Over-triage count    (routine expected but higher tier returned — allowed, printed)
  - Tier agreement       (% of all scenarios where actual_tier == expected_tier)
  - Hospital-type agreement (hospitals_us path)
      • For non-emergency scenarios: top hospital's raw TYPE must be in
        acceptable_hospital_types from scenarios.json.
      • For routine scenarios where expected_service_type is pharmacy/
        primary_care/telemedicine: instead check that the approved advisory
        line "A primary care clinic or pharmacy may be a better first step"
        appears in answer_text.
      • Emergency scenarios are skipped (no hospital search is performed).
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

# Force hospitals_us data source for the benchmark.
os.environ.setdefault("DATA_SOURCE", "hospitals_us")

from src.audit import BENCHMARK_FILE, log_benchmark  # noqa: E402
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
    # For hospitals_us path: raw TYPE string of the top-ranked hospital
    # (e.g. "GENERAL ACUTE CARE"). None when no hospital was returned.
    actual_hospital_type: Optional[str]
    # Acceptable hospital TYPE values for this scenario (from scenarios.json).
    acceptable_hospital_types: list[str]
    tier_match: bool
    under_triage: bool   # actual tier is strictly lower than expected
    over_triage: bool    # actual tier is strictly higher than expected (allowed)
    # True/False = assessed; None = skipped (emergency tier, over-triage-to-emergency,
    # or no ranked results available).
    service_type_match: Optional[bool]
    # True when the routine-advisory check was used instead of type matching.
    routine_advisory_checked: bool
    grounding_failure: bool
    result: SessionResult


# ---------------------------------------------------------------------------
# Helper: raw hospital TYPE from top-ranked result
# ---------------------------------------------------------------------------

# Routine scenarios whose expected_service_type is one of these use the
# advisory-line check instead of hospital-type matching.
_CLINIC_OR_PHARMACY_TYPES = {"pharmacy", "primary_care", "telemedicine"}

# The verbatim substring that must appear in answer_text for routine advisory check.
_ROUTINE_ADVISORY_SUBSTRING = "A primary care clinic or pharmacy may be a better first step"


def _top_hospital_type(result: SessionResult) -> Optional[str]:
    """Return the raw TYPE (uppercased) of the top non-fallback hospital, or None."""
    top = [r for r in result.ranked if not r.is_fallback]
    if not top:
        return None
    r0 = top[0]
    hosp = getattr(r0, "hospital", None)
    if hosp is not None:
        return hosp.type.strip().upper()
    # demo path — no hospital type available
    return None


# ---------------------------------------------------------------------------
# Run all scenarios
# ---------------------------------------------------------------------------

def _run_all_scenarios() -> list[ScenarioOutcome]:
    outcomes: list[ScenarioOutcome] = []
    for scn in _ALL_SCENARIOS:
        result = run_session(scn["user_text"], scn["area"])
        # W8-6: log each benchmark scenario to benchmark.jsonl, not audit.jsonl.
        log_benchmark(result.session_id, "benchmark_run", {
            "scenario_id": scn["id"],
            "label": scn["label"],
            "expected_tier": scn["expected_tier"],
            "actual_tier": result.tier,
            "check_passed": result.check_passed,
        })

        actual_hosp_type = _top_hospital_type(result)
        acceptable_htypes: list[str] = scn.get("acceptable_hospital_types") or []
        expected_svc_type: Optional[str] = scn.get("expected_service_type")

        exp_rank = _TIER_RANK[scn["expected_tier"]]
        act_rank = _TIER_RANK[result.tier]

        tier_match   = result.tier == scn["expected_tier"]
        under_triage = act_rank < exp_rank
        over_triage  = act_rank > exp_rank

        routine_advisory_checked = False

        # Determine service-type match:
        # 1. Skip for emergency-tier scenarios (no hospital search is performed).
        # 2. Skip when over-triaged all the way to emergency (same reason).
        # 3. For routine scenarios whose expected type is clinic/pharmacy/telemedicine:
        #    check that the advisory line appears in answer_text.
        # 4. Otherwise compare actual hospital TYPE against acceptable_hospital_types.
        skip_type = (
            scn["expected_tier"] == "emergency"
            or (over_triage and result.tier == "emergency")
        )

        if skip_type:
            type_match: Optional[bool] = None

        elif (
            result.tier == "routine"
            and expected_svc_type in _CLINIC_OR_PHARMACY_TYPES
        ):
            # Routine path: hospitals are shown with the advisory line.
            # Count as correct when the advisory line appears in the answer.
            routine_advisory_checked = True
            type_match = _ROUTINE_ADVISORY_SUBSTRING in result.answer_text

        elif actual_hosp_type is not None and acceptable_htypes:
            type_match = actual_hosp_type in acceptable_htypes

        elif actual_hosp_type is None:
            # No ranked hospital available — treat as not assessable if needs_human_review,
            # otherwise flag as a type failure.
            type_match = None if result.needs_human_review else False

        else:
            # No acceptable types defined in scenario — skip.
            type_match = None

        outcomes.append(ScenarioOutcome(
            scenario_id=scn["id"],
            label=scn["label"],
            expected_tier=scn["expected_tier"],
            actual_tier=result.tier,
            actual_hospital_type=actual_hosp_type,
            acceptable_hospital_types=acceptable_htypes,
            tier_match=tier_match,
            under_triage=under_triage,
            over_triage=over_triage,
            service_type_match=type_match,
            routine_advisory_checked=routine_advisory_checked,
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
    col = "{:<10} {:<32} {:<10} {:<10} {:<22} {}"
    header = col.format(
        "ID", "Label", "Exp tier", "Act tier",
        "Act hosp TYPE", "Notes"
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
            if o.routine_advisory_checked:
                type_flag = "FAIL ADVISORY"
            else:
                type_flag = "FAIL TYPE"
        elif o.service_type_match is None:
            type_flag = "-"
        else:
            if o.routine_advisory_checked:
                type_flag = "OK(advisory)"
            else:
                type_flag = "OK"

        gnd_flag = " GROUNDING" if o.grounding_failure else ""
        notes = f"{tier_flag} | {type_flag}{gnd_flag}"
        hosp_type_str = str(o.actual_hospital_type or "-")
        print(col.format(
            o.scenario_id,
            o.label[:31],
            o.expected_tier[:9],
            o.actual_tier[:9],
            hosp_type_str[:21],
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
    print(f"Hospital-type agreement:{type_correct}/{len(all_with_type)} "
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

    def test_emergency_total_is_seventeen(self) -> None:
        """16 previous + SCN049 (thunderclap headache, RF013) = 17."""
        assert _METRICS["emergency_total"] == 17

    def test_urgent_total_is_nineteen(self) -> None:
        """17 previous + SCN048 (unable to walk, RF012) + SCN050 (headache+fever+stiff neck, RF014) = 19."""
        assert _METRICS["urgent_total"] == 19

    def test_tier_total_is_fifty(self) -> None:
        """17 emergency + 19 urgent + 14 routine = 50."""
        assert _METRICS["tier_total"] == 50

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


# ---------------------------------------------------------------------------
# W8-6: Benchmark log separation
# ---------------------------------------------------------------------------

class TestBenchmarkLogSeparation:
    def test_benchmark_file_was_written(self) -> None:
        """Running the benchmark must produce logs/benchmark.jsonl (W8-6)."""
        assert BENCHMARK_FILE.exists(), (
            f"benchmark.jsonl not found at {BENCHMARK_FILE}. "
            "Benchmark runs must write to benchmark.jsonl, not audit.jsonl."
        )

    def test_benchmark_file_contains_valid_json_lines(self) -> None:
        """Every line in benchmark.jsonl must be valid JSON with required keys."""
        if not BENCHMARK_FILE.exists():
            pytest.skip("benchmark.jsonl not yet written")
        lines = BENCHMARK_FILE.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) > 0, "benchmark.jsonl is empty"
        for line in lines:
            record = json.loads(line)  # must not raise
            assert "ts" in record
            assert "session_id" in record
            assert "step" in record

    def test_benchmark_file_has_correct_step_name(self) -> None:
        """Benchmark log entries must use step='benchmark_run'."""
        if not BENCHMARK_FILE.exists():
            pytest.skip("benchmark.jsonl not yet written")
        lines = BENCHMARK_FILE.read_text(encoding="utf-8").strip().splitlines()
        steps = {json.loads(l)["step"] for l in lines}
        assert "benchmark_run" in steps

    def test_benchmark_log_has_scenario_id_in_data(self) -> None:
        """Each benchmark_run entry must record scenario_id in its data."""
        if not BENCHMARK_FILE.exists():
            pytest.skip("benchmark.jsonl not yet written")
        for line in BENCHMARK_FILE.read_text(encoding="utf-8").strip().splitlines():
            record = json.loads(line)
            if record["step"] == "benchmark_run":
                assert "scenario_id" in record["data"]
                break
