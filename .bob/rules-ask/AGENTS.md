# AGENTS.md — Ask mode

This file provides guidance to agents when working with code in this repository.

## Non-Obvious Context

- **`src/` and `tests/` are empty stubs** (only `.gitkeep`). The app code has not been built yet; the spec is in `docs/SPEC.md`.
- **Canonical design reference is `docs/SPEC.md`**, not the README. The README is a quickstart; SPEC.md contains the 8-step core flow, data file schemas, and the definition of done.
- **LLM is optional by design.** The app is built to work entirely offline; watsonx.ai is an enhancement only. Answers about "how the LLM is used" must always acknowledge the offline fallback path.
- **Two separate rule sources:** project workspace rules live in `.bob/rules/01-carecompass.md`; AGENTS.md files live in `.bob/rules-*/`. The workspace rules are enforced automatically by Bob.
