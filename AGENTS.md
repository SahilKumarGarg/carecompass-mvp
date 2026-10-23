# AGENTS.md

This file provides guidance to agents when working with code in this repository.

## Stack
Python 3.11+, Streamlit chat UI, pytest, JSON data files. LLM (watsonx.ai) is **optional** — every LLM step must have an offline keyword/template fallback.

## Commands
```bash
streamlit run app.py          # run the app
pytest                        # run all tests
pytest tests/test_foo.py      # run a single test file
pytest tests/test_foo.py::test_bar  # run a single test function
```

## Critical Rules (from .bob/rules/01-carecompass.md)
- **Safety logic must be plain Python reading JSON config** — never implement red-flag rules, urgency tiers, emergency messages, or the ranking formula solely inside an LLM prompt.
- **Emergency path must work with zero LLM calls and zero network** — pure in-process Python + data files only.
- **Never invent service facts** — every facility name, phone number, and opening hours entry must come from `data/services.json`.
- **No diagnosis, conditions, medicines, or treatments** — keep a banned-phrases list and test it.
- **Do not add new libraries** without asking the user first.
- After every change, run `pytest` and report the result before continuing.

## Data Files (all synthetic, must be labelled "demo data")
- `data/services.json` — ~20 services in 5 demo areas
- `data/red_flags.json` — red-flag rule categories with phrases and urgency tier
- `data/messages.json` — verbatim emergency/urgent messages (used as-is, not reworded)
- `data/scenarios.json` — ~12 test scenarios with expected tier and service type

## Code Style
- Modules stay under ~200 lines; split if larger.
- All functions must have type hints and a short docstring.
- Every function gets at least one test in `tests/`.
- Secrets only in `.env` (never committed); copy from `.env.example`.
- Audit every step to `logs/audit.jsonl` (file is gitignored, folder is kept).

## Urgency Tier Logic
Three tiers: `emergency` > `urgent` > `routine`. Highest tier wins. Urgent cases weight distance more heavily in the ranking formula. Emergency tier skips service search entirely and shows the verbatim message from `data/messages.json` plus `EMERGENCY_NUMBER` from `.env`.
