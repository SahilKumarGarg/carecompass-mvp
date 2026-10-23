# AGENTS.md — Plan mode

This file provides guidance to agents when working with code in this repository.

## Non-Obvious Architectural Constraints

- **Emergency path is a hard architectural boundary:** it must never acquire a dependency on the LLM layer or any network call. Plan accordingly — the call graph from "safety check" to "show emergency message" must be entirely in-process Python.
- **Safety tier resolution is highest-wins, not first-wins.** Any planning that touches the safety check or intake steps must account for this: a single emergency phrase in an otherwise routine message elevates the whole case.
- **The ranking formula differs by tier.** Urgent cases weight distance more heavily; routine cases weight fit and data freshness more. Any feature touching recommendations must preserve both ranking paths.
- **Final-check is a validation loop with a retry cap of 2.** If the final check fails twice, the UI shows a "needs human review" notice — not a retry prompt. Plan features around this hard cap.
- **Handoff summary is consent-gated.** The summary is created only after explicit user consent (button click). No plan should generate or store handoff text before that gate.
- **All service data is read from `data/services.json` at runtime** — no caching layer, no DB. Plans that need performance gains must stay within this constraint.
- **`src/` is currently empty.** Any plan that references existing modules is forward-looking; confirm the module exists before treating it as a dependency.
