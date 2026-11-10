# CareCompass — Test Suite Reference

> The test suite is **fully offline**. No network access, no watsonx.ai credentials, and
> no real data are needed to run it. The suite exercises `DATA_SOURCE=demo` (synthetic
> services in `data/services.json`) throughout; it does not load `data/hospitals_clean.csv`.

---

## Running the tests

```powershell
# Full suite (run from project root)
.venv\Scripts\pytest.exe tests/ -v --tb=short

# Single test file
.venv\Scripts\pytest.exe tests/test_safety.py -v --tb=short

# Single test by name
.venv\Scripts\pytest.exe tests/test_safety.py -k "test_empty_string_returns_routine"

# Benchmark only — prints a summary table
.venv\Scripts\pytest.exe tests/test_benchmark.py -v -s
```

> **Critical:** always run pytest via `.venv\Scripts\pytest.exe` from the project root.
> Plain `pytest` or `python -m pytest` will fail with `No module named src` because
> `conftest.py` at the project root adds `src/` to `sys.path`. Plain `pytest` does not
> pick up that `conftest.py`.

---

## Test files

| File | Tests | What it covers |
|---|---|---|
| `test_safety.py` | 82 | `check_red_flags` — all 47 scenarios, tier + rule-ID assertions |
| `test_safety_v2.py` | 57 | Term-group paraphrases, negation window, edge cases for all 11 rules |
| `test_safety_v3.py` | 46 | Additional paraphrase and negation edge cases |
| `test_benchmark.py` | 85 | End-to-end pipeline over all 47 scenarios; hard failure on under-triage |
| `test_safety_proof.py` | 140 | Consent gate, offline emergency, red-team inputs (injection, diagnosis fishing, dose questions, misspellings) |
| `test_banned_phrases.py` | 85 | Blocked/safe sentences; end-to-end urgent answers for SCN005–SCN017 |
| `test_orchestrator.py` | 58 | Full `run_session()` for all three tiers; audit log; emergency config; human-review branching |
| `test_checks.py` | 36 | Each of the 13 `final_check` sub-checks independently |
| `test_finder.py` | 22 | Haversine formula, service filtering, distance computation |
| `test_advisor.py` | 60 | Each scoring function, `rank()`, reason builder, stale warning |
| `test_intake.py` | 69 | Offline field extraction (who, concern, duration, severity, denied symptoms), follow-up questions |
| `test_locations.py` | 86 | `list_states()`, `list_counties()`, `list_cities()`, `resolve_location()` — widening logic; hospital scoring and ranking |
| `test_prepare_hospitals.py` | 15 | `prepare()` in `scripts/prepare_hospitals.py` — status filter, coord filter, dedup, column normalisation |
| `test_grounding_new.py` | 57 | Grounding checks for the `hospitals_us` path |
| `test_app_button_spec.py` | 16 | `get_button_spec()` in `app.py` — correct tier-specific button (call, directions, create summary) |
| `test_summarize_user_test.py` | 38 | User-test walkthrough summariser |

---

## Hard requirements enforced by the benchmark

These three assertions in `tests/test_benchmark.py` must always pass zero failures:

| Assertion | Test name |
|---|---|
| No under-triage | `test_no_under_triage`, `test_each_high_priority_scenario_not_under_triaged` |
| Emergency recall = 100 % (16/16) | `test_emergency_recall_is_100_percent` |
| Grounding failures = 0 | counted in benchmark summary; `check_passed=False` + `needs_human_review=True` |

952 tests total. **Under-triage = hard test failure.** A single scenario where
`actual_tier` is lower than `expected_tier` fails the entire suite. Over-triage (higher
tier than expected) is allowed — the system errs on the side of caution.

---

## Current benchmark results

Scenarios: 47 (16 emergency, 17 urgent, 14 routine).

```
Emergency recall:       16/16  (100%)
Urgent recall:          17/17  (100%)
Under-triage:            0/47         [MUST be 0]
Over-triage (allowed):   3/47         [SCN008, SCN030, SCN037]
Tier agreement:         44/47  (94%)
Service-type agreement: 26/28  (93%)
Grounding failures:      0/47         [MUST be 0]
```

The 3 over-triage cases (SCN008, SCN030, SCN037) involve asthma-worsening or formally-
described fever where breathing/fever phrases incidentally match RF002, raising the tier
to emergency. This is safe-side over-triage.

---

## Test data note

All tests run with `DATA_SOURCE=demo` (set implicitly by the absence of a `DATA_SOURCE`
env var that overrides the default, or explicitly in `conftest.py`). The synthetic
`data/services.json` directory is the data source for all scenario, benchmark, grounding,
and orchestrator tests. Tests in `test_locations.py` and `test_prepare_hospitals.py` test
the real-dataset path but do so against a small in-memory fixture, not the full CSV.

The US hospitals dataset (`data/hospitals_clean.csv`) is not committed to the repository
and is not required to run the test suite. See [`docs/DATA.md`](DATA.md) for instructions
on generating it.

---

## Test class structure (selected files)

### `test_safety_proof.py`

| Class | Input type | What is asserted |
|---|---|---|
| `TestEmptyInput` | `""`, whitespace, `"?"`, `"\x00"` | No crash; `tier=routine`; non-empty answer |
| `TestPromptInjection` | 8 injection strings | No banned phrase in answer; any emergency tier has a genuine rule hit |
| `TestDiagnosisFishingQuestions` | `"What disease do I have?"` × 10 | No diagnosis in answer; `tier=routine` for pure questions |
| `TestDoseAndMedicationQuestions` | `"How much ibuprofen should I take?"` × 10 | No medication advice in answer; `tier=routine` |
| `TestMisspelledInputs` | Intact + misspelled variants | Misspelled key words stay routine; intact key words fire the right rule |
| `TestBannedPhraseGuard` | Simulated LLM outputs | 10 diagnosis outputs blocked; 10 treatment outputs blocked; 7 condition-naming outputs blocked |
| `TestOfflineEmergency` | All 7 emergency rule families | `tier=emergency`; `candidates=[]`; `ranked=[]`; answer contains emergency number; no banned phrases |
| `TestConsentGate` | All three tiers | `handoff_summary=None` after `run_session()`; only set after explicit `create_handoff_summary()` call |

### `test_benchmark.py`

Parametrised over all 47 entries in `data/scenarios.json`. Each scenario specifies
`expected_tier`, optionally `expected_red_flag_id`, and optionally
`acceptable_service_types`. The benchmark:

1. Runs `run_session()` for each scenario.
2. Asserts no under-triage.
3. For scenarios with an `expected_red_flag_id`, asserts the rule fired.
4. For scenarios with `acceptable_service_types`, asserts the top recommendation's type
   is in the acceptable set.
5. Asserts `check_passed=True` or `needs_human_review=False` (grounding).

---

## Adding new tests

### For a new red-flag rule

1. Add an entry to `data/red_flags.json` (next id: RF012).
2. Add a `TestRF12YourRuleName` class with plain, formal, and typo variants to
   `tests/test_safety_v2.py`.
3. Add a scenario to `data/scenarios.json` with `"expected_red_flag_id": "RF012"`.
4. If the tier count changes, update the hardcoded tier-count assertions in
   `tests/test_benchmark.py`.

### For a new banned phrase

1. Add the phrase to `data/banned_phrases.json` in the appropriate category.
2. Add at least one blocked-sentence test to `tests/test_banned_phrases.py`.
3. Run the full suite; confirm no existing urgent-answer tests break (avoid overly broad
   fragments — see the "Avoid overly broad fragments" note in `docs/GOVERNANCE.md`).

### For the hospitals_us path

Add tests to `tests/test_locations.py` (location resolution) or
`tests/test_grounding_new.py` (full pipeline grounding). Use in-memory fixtures rather
than loading the full CSV to keep tests offline and fast.
