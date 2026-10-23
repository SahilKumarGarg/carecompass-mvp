"""Tests for src/safety.py — uses emergency and urgent scenarios from data/scenarios.json."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.safety import SafetyResult, check_red_flags

# ---------------------------------------------------------------------------
# Load scenarios once at module import time.
# ---------------------------------------------------------------------------
_SCENARIOS_PATH = Path(__file__).parent.parent / "data" / "scenarios.json"

with _SCENARIOS_PATH.open(encoding="utf-8") as _fh:
    _ALL_SCENARIOS = json.load(_fh)["scenarios"]

_EMERGENCY_SCENARIOS = [s for s in _ALL_SCENARIOS if s["expected_tier"] == "emergency"]
_URGENT_SCENARIOS = [s for s in _ALL_SCENARIOS if s["expected_tier"] == "urgent"]
_ROUTINE_SCENARIOS = [s for s in _ALL_SCENARIOS if s["expected_tier"] == "routine"]


# ---------------------------------------------------------------------------
# Parametrised tests driven by scenarios.json
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scenario", _EMERGENCY_SCENARIOS, ids=[s["id"] for s in _EMERGENCY_SCENARIOS])
def test_emergency_scenarios_return_emergency_tier(scenario: dict) -> None:
    """Every emergency scenario must produce tier='emergency'."""
    result = check_red_flags(scenario["user_text"])
    assert result.tier == "emergency", (
        f"{scenario['id']} ({scenario['label']}): expected emergency, got {result.tier!r}. "
        f"Hits: {result.hits}"
    )


@pytest.mark.parametrize("scenario", _EMERGENCY_SCENARIOS, ids=[s["id"] for s in _EMERGENCY_SCENARIOS])
def test_emergency_scenarios_hit_expected_rule(scenario: dict) -> None:
    """Emergency scenarios must trigger the expected red-flag rule ID."""
    result = check_red_flags(scenario["user_text"])
    hit_ids = {h.rule_id for h in result.hits}
    assert scenario["expected_red_flag_id"] in hit_ids, (
        f"{scenario['id']}: expected rule {scenario['expected_red_flag_id']} to fire. "
        f"Actual hits: {result.hits}"
    )


# Only scenarios that have a known red-flag rule ID can be tested via phrase matching.
# Scenarios where expected_red_flag_id is None (e.g. urgent-by-clinical-judgement)
# are intentionally excluded here — they rely on the intake/LLM layer, not check_red_flags.
_URGENT_WITH_RULE = [s for s in _URGENT_SCENARIOS if s["expected_red_flag_id"] is not None]


@pytest.mark.parametrize("scenario", _URGENT_WITH_RULE, ids=[s["id"] for s in _URGENT_WITH_RULE])
def test_urgent_scenarios_with_rule_return_at_least_urgent_tier(scenario: dict) -> None:
    """Urgent scenarios that have a matching red-flag rule must return tier urgent or emergency."""
    result = check_red_flags(scenario["user_text"])
    assert result.tier in {"urgent", "emergency"}, (
        f"{scenario['id']} ({scenario['label']}): expected urgent or emergency, got {result.tier!r}. "
        f"Hits: {result.hits}"
    )


@pytest.mark.parametrize(
    "scenario",
    [s for s in _URGENT_SCENARIOS if s["expected_red_flag_id"] is not None],
    ids=[s["id"] for s in _URGENT_SCENARIOS if s["expected_red_flag_id"] is not None],
)
def test_urgent_scenarios_hit_expected_rule(scenario: dict) -> None:
    """Urgent scenarios with a specified rule ID must trigger that rule."""
    result = check_red_flags(scenario["user_text"])
    hit_ids = {h.rule_id for h in result.hits}
    assert scenario["expected_red_flag_id"] in hit_ids, (
        f"{scenario['id']}: expected rule {scenario['expected_red_flag_id']} to fire. "
        f"Actual hits: {result.hits}"
    )


@pytest.mark.parametrize("scenario", _ROUTINE_SCENARIOS, ids=[s["id"] for s in _ROUTINE_SCENARIOS])
def test_routine_scenarios_return_routine_tier(scenario: dict) -> None:
    """Routine scenarios must not trigger any red-flag rules."""
    result = check_red_flags(scenario["user_text"])
    assert result.tier == "routine", (
        f"{scenario['id']} ({scenario['label']}): expected routine, got {result.tier!r}. "
        f"Hits: {result.hits}"
    )


# ---------------------------------------------------------------------------
# Unit tests for check_red_flags behaviour
# ---------------------------------------------------------------------------

def test_no_match_returns_routine_and_empty_hits() -> None:
    """Clean input with no red-flag phrases returns routine with no hits."""
    result = check_red_flags("I have a mild headache that started yesterday.")
    assert result.tier == "routine"
    assert result.hits == []


def test_returns_safety_result_type() -> None:
    """Return type is always SafetyResult."""
    result = check_red_flags("chest pain")
    assert isinstance(result, SafetyResult)


def test_case_insensitive_matching() -> None:
    """Matching ignores case."""
    result = check_red_flags("CHEST PAIN")
    assert result.tier == "emergency"


def test_punctuation_ignored() -> None:
    """Punctuation between words does not prevent a match."""
    result = check_red_flags("I have chest, pain.")
    # 'chest, pain' normalises to 'chest  pain' then 'chest pain' — should match
    assert result.tier == "emergency"


def test_highest_tier_wins_when_multiple_rules_match() -> None:
    """If both urgent and emergency rules match, tier must be 'emergency'."""
    # Combines self-harm thoughts (urgent) with chest pain (emergency)
    result = check_red_flags("I've been having thoughts of hurting myself and now I have chest pain.")
    assert result.tier == "emergency"
    hit_ids = {h.rule_id for h in result.hits}
    assert "RF001" in hit_ids   # chest_pain
    assert "RF009" in hit_ids   # self_harm_thoughts


def test_hits_sorted_highest_tier_first() -> None:
    """Hits list is ordered with highest-severity tier first."""
    result = check_red_flags("I've been having thoughts of hurting myself and I have chest pain.")
    assert result.hits[0].tier == "emergency"


def test_each_rule_category_fires_at_most_once() -> None:
    """Repeating a phrase multiple times does not create duplicate hits for the same rule."""
    result = check_red_flags("chest pain chest pain chest pain")
    rf001_hits = [h for h in result.hits if h.rule_id == "RF001"]
    assert len(rf001_hits) == 1


def test_empty_string_returns_routine() -> None:
    """Empty input returns routine tier."""
    result = check_red_flags("")
    assert result.tier == "routine"
    assert result.hits == []
