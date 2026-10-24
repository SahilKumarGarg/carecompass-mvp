# AGENTS.md — Plan mode (architecture/design)

This file provides guidance to agents when working with code in this repository.

## Non-obvious architectural constraints

- **Safety check is a pure function with no side effects** — `check_red_flags()` reads JSON at module load; it never writes, never calls LLM, never accesses network. This must remain true — it is the fallback guarantee.
- **Emergency tier is a hard short-circuit** — when `safety.tier == "emergency"`, `run_session()` returns immediately with no candidates, no ranked results, no LLM call. Any new pipeline step that must run on emergency cases must be inserted before the emergency-return branch, not after.
- **`final_check()` is stateless and reentrant** — it does not modify any shared state; can be called multiple times safely. The retry loop is in the orchestrator, not inside `final_check()`.
- **`term_groups` semantics are conjunction-of-disjunctions**: ALL groups must fire (AND), each group fires if ANY synonym matches (OR). This means adding a third group to an RF rule makes it harder to trigger (more conditions). Adding synonyms to an existing group makes it easier.
- **Negation check is window-3 token look-back only** — `_is_negated()` only looks at the 3 tokens immediately before the matched synonym in the normalised text. Long-distance negation ("I never had any chest pain in my life") is NOT caught — by design (safe-side: false positives are preferred over false negatives in safety).
- **Phrase matching has no negation** — intentional. Phrases like `"chest pain"` are assumed unambiguous. Adding negation to phrase matching would risk false negatives on emergencies.
- **Over-triage is safe-side; under-triage is a hard failure** — the benchmark enforces `under_triage_count == 0`. Never loosen this. Over-triage (urgent→emergency) is acceptable and reported-only.
- **`data/care_levels.json` routes concern keywords to service types** — this keyword→type mapping is the only routing mechanism in the offline path. `finder.py` reads it to select which `type` of service to search.
- **`advisor.py` scoring uses different weight profiles for `urgent` vs `routine`** — urgent weights distance more heavily. The profiles are plain dicts in the module, not configurable from JSON yet.
- **Audit log (`logs/audit.jsonl`) writes one JSON line per pipeline step** — it is append-only and never read back by the app. Structured for external analysis only.
