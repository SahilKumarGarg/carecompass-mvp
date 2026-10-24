# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Commands

```bash
# All tests (run from project root)
.venv\Scripts\pytest.exe tests/ -v --tb=short

# Single test file
.venv\Scripts\pytest.exe tests/test_safety.py -v --tb=short

# Single test by name
.venv\Scripts\pytest.exe tests/test_safety.py -v -k "test_empty_string_returns_routine"

# Benchmark only (slow, runs all 17 scenarios)
.venv\Scripts\pytest.exe tests/test_benchmark.py -v -s

# Run the app
.venv\Scripts\streamlit.exe run app.py
```

**Critical**: pytest must be run via `.venv\Scripts\pytest.exe` from the project root. Plain `pytest` or `python -m pytest` will fail with `No module named src`. The `conftest.py` at root inserts the project root into `sys.path`.

## Project rules (non-negotiable)

- **Safety logic is plain Python reading JSON config** — never implement red-flag rules only in a prompt.
- **Emergency path must work with zero LLM + zero network** — always. Emergency exit is in [`orchestrator.py`](src/orchestrator.py) before any LLM call.
- **Never invent services, phone numbers, or opening hours** — every fact must come from [`data/services.json`](data/services.json).
- **No diagnosis, conditions, medicines, or treatments** — enforced by [`checks.py:final_check()`](src/checks.py) against [`data/banned_phrases.json`](data/banned_phrases.json). Add new banned phrases there, not in code.
- **Demo data only** — all `data/*.json` files carry `"demo_data": true`. Never use real personal data.
- **Secrets in `.env`** (never committed) — see [`.env.example`](.env.example) for `WATSONX_*` and `EMERGENCY_NUMBER` keys.
- **No new libraries** without asking the user first.

## Architecture: 8-step pipeline

`run_session()` in [`src/orchestrator.py`](src/orchestrator.py) drives the pipeline:

```
describe → understand → safety → find → compare → final_check (≤2 retries) → present → handoff
```

- **Safety fires before any LLM call.** `emergency` tier exits immediately (no candidates, no LLM).
- `check_red_flags()` in [`src/safety.py`](src/safety.py) is purely deterministic: reads [`data/red_flags.json`](data/red_flags.json), applies phrase match + term_groups conjunction match.
- `final_check()` in [`src/checks.py`](src/checks.py) runs 6 checks: service IDs, service names, phones, banned phrases, last_verified date, urgent language. Fails → revise up to 2 times, then sets `needs_human_review=True`.

## Red-flag rule format (`data/red_flags.json`)

Each rule has `id`, `name`, `tier` (`emergency` or `urgent`), optional `phrases` array, optional `term_groups` array.

- **`phrases`**: any single substring match fires the rule (no negation applied — safe-side).
- **`term_groups`**: ALL groups must each have ≥1 synonym match; negation-aware window check (3 tokens, negators: no/not/without/never/dont/doesnt/didnt).
- Either strategy firing is enough. Both strategies are independent.
- Highest tier across all firing rules wins. One hit per rule (deduplicated by `id`).

## Code style

- Python 3.14, type hints throughout, short module docstrings.
- `from __future__ import annotations` at top of every `src/` file.
- Imports: stdlib → then `from src.module import ...` (no `import src.module`).
- Modules capped at ~200 lines.
- Data paths: always use `Path(__file__).parent.parent / "data" / "filename.json"` — never `os.getcwd()`.
- All data loaded at module import time as a module-level constant (not inside functions).
- Every public function gets at least one test in `tests/`.

## Testing conventions

- Test files mirror source: `tests/test_<module>.py`.
- Test classes group by unit; parametrize over scenario IDs from `data/scenarios.json` where applicable.
- Benchmark (`tests/test_benchmark.py`) runs all 17 scenarios offline. **Under-triage = hard test failure. Over-triage = allowed (just reported).**
- Scenarios with `expected_red_flag_id: null` still have an `expected_tier` — the benchmark checks tiers even when no specific rule ID is pinned.
- `test_safety.py` uses scenarios; `test_safety_v2.py` tests term_groups, paraphrases, and negation exhaustively.

## Adding a new red-flag rule

1. Add an entry to [`data/red_flags.json`](data/red_flags.json) — pick the next `RF0NN` id.
2. Add paraphrase tests to [`tests/test_safety_v2.py`](tests/test_safety_v2.py) in a new `TestRFNNYourRuleName` class.
3. If fixing an under-triage scenario, also update `expected_red_flag_id` in [`data/scenarios.json`](data/scenarios.json) if a specific rule is expected.
