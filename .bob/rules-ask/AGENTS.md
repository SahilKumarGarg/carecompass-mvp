# AGENTS.md — Ask mode (documentation/questions)

This file provides guidance to agents when working with code in this repository.

## Non-obvious documentation context

- **`src/` contains the deterministic pipeline, not the UI** — the Streamlit chat UI is entirely in `app.py` at the root, not in `src/`.
- **LLM is fully optional** — `src/intake.py` has an `_watsonx_available()` guard; every `src/` step has a keyword/template offline fallback. The entire app runs without watsonx configured.
- **`data/messages.json` is the canonical source for all user-facing emergency/urgent text** — do not look for these strings in `src/` or `app.py`.
- **`docs/SPEC.md` defines the 8-step pipeline** — it is the authoritative spec. The README provides a user-facing overview but may lag behind `SPEC.md`.
- **`tests/test_benchmark.py` is both a test file and the performance report** — run it with `-s` to see the full benchmark table printed to stdout. It's the clearest view of current system accuracy.
- **`logs/` dir is committed but `logs/audit.jsonl` is gitignored** — the folder exists in the repo; the actual audit data is local-only.
- **Three urgency tiers**: `emergency` (call 999 / no service search) → `urgent` (same-day, prioritise open 24h) → `routine` (normal GP/pharmacy). `emergency` tier short-circuits the entire pipeline after the safety step.
- **`data/scenarios.json` has 17 scenarios**: 4 emergency, 9 urgent (SCN005–008, SCN013–017), 4 routine. SCN013–017 are the 5 newer scenarios added to expand coverage.
