# CareCompass MVP

CareCompass is a healthcare navigation assistant built for the IBM SkillsBuild programme.
A user describes a health concern in plain language, picks their area from a dropdown, and
the app determines how urgent the situation is, finds matching local services from a demo
directory, and gives one clear next step — including a phone number and the date the service
record was last verified.

**It does not diagnose. It does not suggest medicines or treatments. It directs people to
the right service.**

> ⚠ All data in this repository is entirely synthetic demo data. Nothing here is medical
> advice. Never use this application for real clinical decisions.

---

## Quick start

**Requires Python 3.11 or newer.**

```powershell
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy the env file (leaving values blank uses the fully offline fallback)
copy .env.example .env

# 4. Launch the app
streamlit run app.py
```

The app opens at `http://localhost:8501`.

### Optional: watsonx.ai

Set `WATSONX_API_KEY`, `WATSONX_URL`, and `WATSONX_PROJECT_ID` in `.env`.
When present the intake step uses **ibm/granite-13b-instruct-v2** for structured field
extraction, and the advisor uses it to reword service recommendation reasons.
**Every LLM step has an offline fallback — watsonx.ai is never required.**

Set `EMERGENCY_NUMBER` to the local emergency number shown in emergency messages (default
demo value: `[DEMO-999]`). Set `EMERGENCY_REGION` for the region label (default: `DEMO REGION`).

---

## Running the tests

```powershell
# Full suite (647 tests, ~3.7 s, fully offline)
.venv\Scripts\pytest.exe tests/ -v --tb=short

# Single test file
.venv\Scripts\pytest.exe tests/test_safety.py -v

# Single test by name
.venv\Scripts\pytest.exe tests/test_safety.py -k "test_empty_string_returns_routine"

# Benchmark with printed summary table
.venv\Scripts\pytest.exe tests/test_benchmark.py -v -s
```

> **Note:** always run pytest via `.venv\Scripts\pytest.exe` from the project root.
> Plain `pytest` fails with `No module named src` because `conftest.py` adds `src/` to
> `sys.path` only through the project-root `conftest.py`.

The test suite is fully offline — no credentials, no network — and covers:

| File | Tests | What it covers |
|---|---|---|
| `test_safety.py` | 56 | Red-flag rule matching across all scenarios |
| `test_safety_v2.py` | 44 | Term-group paraphrases, negation, edge cases |
| `test_benchmark.py` | 83 | End-to-end pipeline over all 47 scenarios |
| `test_safety_proof.py` | 140 | Consent gate, offline emergency, red-team cases |
| `test_banned_phrases.py` | 38 | Blocked/safe sentences, SCN005–SCN017 answers |
| `test_orchestrator.py` | 64 | Session schema, all paths, audit log, config |
| `test_checks.py` | 34 | Each final_check sub-check independently |
| `test_finder.py` | 24 | Haversine, service filtering, distance |
| `test_advisor.py` | 58 | Scoring functions, ranking, reason builder |
| `test_intake.py` | 66 | Offline field extraction, follow-up questions |

---

## The 8-step pipeline

Each user turn passes through a fixed pipeline. Every step and its outcome is appended to
`logs/audit.jsonl` as a JSON line.

```
describe → understand → safety → find → compare → final_check → present → handoff
```

| # | Step | What happens |
|---|---|---|
| 1 | **Describe** | User types a concern and picks an area from the dropdown. |
| 2 | **Understand** | Intake extracts who the concern is about, the concern, duration, and severity cues. Up to 2 follow-up questions are asked when fields are missing. |
| 3 | **Safety check** | Deterministic red-flag rules (pure Python, no LLM) scan the text using phrase lists and negation-aware synonym groups. Highest tier wins. **Emergency exits here with no service search and no LLM call.** |
| 4 | **Find** | The case maps to preferred service types. Services in the chosen area are loaded from `data/services.json`. Straight-line distance is estimated from the area centre using the Haversine formula. |
| 5 | **Compare** | Candidates are scored on type fit, distance, 24-hour availability, and data freshness. Urgent cases weight distance and availability more heavily. Top 3 recommendations plus one fallback are selected. |
| 6 | **Final check** | Every service ID, name, and phone in the answer is verified against `data/services.json`. Banned diagnosis/treatment phrases are scanned. The last-verified date must appear. Urgent answers must contain urgent-action language. On failure the answer is revised and re-checked — at most 2 retries, then a human-review notice is shown. |
| 7 | **Present** | The recommendation is displayed: service card, distance estimate, opening hours, phone, last-verified date, and reason. |
| 8 | **Handoff** | Only after the user clicks **Yes**, a short plain-text summary is created. Clicking **No** creates nothing. No summary is ever produced without explicit user consent. |

---

## Project layout

```
carecompass-mvp/
├── app.py                  Streamlit chat UI (areas: Greenfield, Riverside, Northgate,
│                               Eastfield, Westbury)
├── requirements.txt
├── .env.example            Template for WATSONX_* and EMERGENCY_* env vars
├── conftest.py             Adds src/ to sys.path for pytest
├── AGENTS.md               Agent-mode coding rules
│
├── data/                   Synthetic demo data (all carry "demo_data": true)
│   ├── services.json       20 services across 5 demo areas
│   ├── red_flags.json      11 red-flag rules (RF001–RF011)
│   ├── messages.json       Verbatim emergency/urgent/review messages
│   ├── scenarios.json      47 test scenarios (16 emergency, 17 urgent, 14 routine)
│   ├── banned_phrases.json Diagnosis and treatment phrases the app must never produce
│   ├── care_levels.json    Keyword → service-type preference mapping
│   └── area_centres.json   Approximate centre coordinates per area
│
├── src/
│   ├── safety.py           Red-flag phrase matching — deterministic, no LLM
│   ├── intake.py           Case extraction — offline + optional watsonx.ai
│   ├── finder.py           Service filtering and distance estimation
│   ├── advisor.py          Scoring, ranking, and reason building
│   ├── checks.py           Final-check validation (6 sub-checks)
│   ├── orchestrator.py     8-step state machine; emergency exit; consent gate
│   └── audit.py            JSONL audit log writer (logs/audit.jsonl)
│
├── tests/                  pytest suite (647 tests, fully offline)
├── docs/
│   ├── SPEC.md             Original technical specification
│   ├── USER_GUIDE.md       End-user walkthrough
│   ├── ARCHITECTURE.md     Component design and data flows
│   └── GOVERNANCE.md       Safety rules, consent gate, banned phrases, testing strategy
└── logs/                   audit.jsonl and benchmark.jsonl written here at runtime
```

---

## Data files

| File | Contents |
|---|---|
| `data/services.json` | 20 synthetic services (4 per area). Types: `pharmacy`, `primary_care`, `hospital_emergency`, `specialist`, `telemedicine`. All names, phones, and addresses are invented. |
| `data/red_flags.json` | 11 red-flag rules (RF001–RF011). 8 emergency, 3 urgent. Each rule has `phrases` (substring match) and/or `term_groups` (conjunction of synonym groups, negation-aware). |
| `data/messages.json` | Verbatim messages for emergency, urgent, needs-human-review, and handoff prompts. `{emergency_number}` and `{emergency_region}` are replaced at runtime. |
| `data/scenarios.json` | 47 test scenarios with `expected_tier`, `expected_red_flag_id`, and `acceptable_service_types`. Used by the benchmark. |
| `data/banned_phrases.json` | 3 categories (BP001 diagnosis, BP002 treatment/dosage, BP003 condition naming). `final_check` blocks any answer containing these phrases. |
| `data/care_levels.json` | Keyword → preferred service types list, used by `finder.map_to_service_types()`. |
| `data/area_centres.json` | Lat/lon centre of each demo area, used for Haversine distance estimation. |

Every data file carries `"demo_data": true`. No real patient data is used anywhere.

---

## Known limits of this MVP

- **Safety detection is keyword-based.** Indirect descriptions of urgent conditions (e.g.
  "my asthma inhaler stopped working") may not escalate without the watsonx.ai intake step.
- **Open-hours are not parsed in real time.** A service not marked `open_24h` scores 0.5
  regardless of whether it is actually open right now.
- **Distance is from the area centre, not the user's exact location.** All five demo areas
  use synthetic coordinates; distances are plausible but not real.
- **Follow-up questions are generated but not yet surfaced in the UI.** The orchestrator
  detects missing fields and produces a question, but `app.py` does not display it before
  running the pipeline.
- **The final-check revision is cosmetic.** Appending a disclaimer and retrying passes the
  urgent-advice check but does not fix structural problems such as a missing phone number.
- **Five demo areas only.** An unknown area name returns no candidates.

---

## Docs

- [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) — step-by-step guide for end users
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — component design, scoring weights, data flows
- [`docs/GOVERNANCE.md`](docs/GOVERNANCE.md) — safety rules, consent gate, banned phrases, testing
- [`docs/SPEC.md`](docs/SPEC.md) — original technical specification
