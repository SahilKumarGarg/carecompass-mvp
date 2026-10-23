# AGENTS.md — Agent (coding) mode

This file provides guidance to agents when working with code in this repository.

## Non-Obvious Coding Rules

- **Safety/ranking logic lives in Python, not prompts.** Red-flag rules, urgency tier resolution, the ranking formula, and emergency messages all come from JSON config files read by plain Python. If you're tempted to embed this logic in a prompt, don't.
- **Emergency path is LLM-free and network-free.** It must be reachable via a pure Python code path. No `if llm_available` guard — it always runs offline.
- **Service facts are read-only from `data/services.json`.** Never hardcode or fabricate a facility name, number, or hours. The final-check step (step 6 in the spec) validates every service ID against this file.
- **Banned-phrases list must be code-tested.** There must be at least one pytest test that asserts diagnosis/medicine/condition wording does not appear in generated output.
- **Module size limit: ~200 lines.** Split proactively rather than waiting for a review comment.
- **Every new function → at least one test in `tests/`.** Run `pytest` after every change and paste the result before moving on.
- **No new pip dependencies** without explicit user approval; `requirements.txt` is intentionally minimal (streamlit, pytest, python-dotenv, optional ibm-watsonx-ai).
- **Audit log format**: `logs/audit.jsonl` (newline-delimited JSON). Folder is committed; file contents are gitignored.
