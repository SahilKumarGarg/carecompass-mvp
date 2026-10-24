# AGENTS.md — Agent mode (coding)

This file provides guidance to agents when working with code in this repository.

## Non-obvious coding rules

- **pytest requires `.venv\Scripts\pytest.exe` from project root.** Plain `pytest` fails. `conftest.py` at root adds `src` to `sys.path` — do not remove it.
- **Emergency exit in `orchestrator.py` must stay before any LLM call** — the branch is at the top of `run_session()`. Adding code between safety-check and the emergency-return will break the offline guarantee.
- **All data loaded at module import time**, not inside functions — `_RED_FLAGS`, `_SERVICES`, etc. are module-level constants. Follow this pattern to avoid repeated file I/O.
- **`phrases` in red_flags rules bypass negation checks (by design).** Only `term_groups` synonyms are negation-aware. This is intentional: phrases are for unambiguous text like "chest pain" where negation is unlikely.
- **Never add a synonym that is too generic to `term_groups`** — e.g., adding `"still"` or `"quite"` to an RF group can accidentally fire on routine inputs. Use standalone `phrases` for unambiguous words (e.g., `"breathless"`).
- **`final_check()` retries at most 2 times** (`_MAX_CHECK_RETRIES = 2` in orchestrator). Each retry builds a revised answer; 3rd failure sets `needs_human_review=True`. Do not increase this limit.
- **Imports use `from src.module import Symbol`** — never `import src.module`. All internal cross-module imports follow this pattern.
- **`data/messages.json` strings are used verbatim** — never reword emergency/urgent messages in code. Edit the JSON to change wording.
- **`banned_phrases.json` must be edited to add new forbidden terms** — `final_check()` loads it at import, not passed as argument.
- **Benchmark `test_non_emergency_scenarios_have_candidates_or_review`** excludes over-triaged-to-emergency scenarios (they follow emergency path by design). Keep this exclusion when the filter is `actual_tier != "emergency"`.
