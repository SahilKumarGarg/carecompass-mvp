# CareCompass MVP

CareCompass is a healthcare navigation assistant built for the IBM SkillsBuild programme.
A user describes a health concern in plain language, selects their US state, county, city,
or ZIP code from dropdowns, and the app determines how urgent the situation is, finds
matching nearby hospitals from a real US hospitals dataset, and gives one clear next step —
including a phone number and the year the record was last validated.

**It does not diagnose. It does not suggest medicines or treatments. It directs people to
the right service.**

> ⚠ This application uses a public US hospitals dataset sourced from Kaggle
> ([US Hospitals by andrewmvd](https://www.kaggle.com/datasets/andrewmvd/us-hospitals)).
> Records were validated between 2013 and 2022; phone numbers and services **may be out of
> date**. The dataset has **no emergency-department field and no opening-hours field** —
> always call ahead to confirm a facility is open and appropriate before travelling.
> **Nothing here is medical advice. Never use this application as a substitute for
> professional clinical judgement.**

---

## Quick start

**Tested on Python 3.14.5. Other versions are untested.**

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

Set `EMERGENCY_NUMBER` to the local emergency number shown in emergency messages (default:
`911`). Set `EMERGENCY_REGION` for the region label (default: `US`).

Set `DATA_SOURCE` to `hospitals_us` (default) to use `data/hospitals_clean.csv`, or `demo`
to fall back to the original `data/services.json` synthetic directory (used by the existing
test suite).

---

## Running the tests

```powershell
# Full suite (952 tests, fully offline)
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
| `test_safety.py` | 82 | Red-flag rule matching across all scenarios |
| `test_safety_v2.py` | 57 | Term-group paraphrases, negation, edge cases |
| `test_safety_v3.py` | 46 | Additional paraphrase and negation edge cases |
| `test_benchmark.py` | 85 | End-to-end pipeline over all 47 scenarios |
| `test_safety_proof.py` | 140 | Consent gate, offline emergency, red-team cases |
| `test_banned_phrases.py` | 85 | Blocked/safe sentences, SCN005–SCN017 answers |
| `test_orchestrator.py` | 58 | Session schema, all paths, audit log, config |
| `test_checks.py` | 36 | Each final_check sub-check independently |
| `test_finder.py` | 22 | Haversine, service filtering, distance |
| `test_advisor.py` | 60 | Scoring functions, ranking, reason builder |
| `test_intake.py` | 69 | Offline field extraction, follow-up questions |
| `test_locations.py` | 86 | State/county/city/ZIP lookups, hospital scoring and ranking |
| `test_grounding_new.py` | 57 | Grounding checks for the hospitals_us path |
| `test_app_button_spec.py` | 16 | Tier-specific button spec (call, directions, create summary) |
| `test_prepare_hospitals.py` | 15 | CSV ingest script — filters, normalisation, dedup |
| `test_summarize_user_test.py` | 38 | User-test walkthrough summariser |

---

## The 8-step pipeline

Each user turn passes through a fixed pipeline. Every step and its outcome is appended to
`logs/audit.jsonl` as a JSON line.

```
describe → understand → safety → find → compare → final_check → present → handoff
```

| # | Step | What happens (hospitals_us path — default) |
|---|---|---|
| 1 | **Describe** | User types a concern and picks State, County, City, and/or ZIP from cascading dropdowns. |
| 2 | **Understand** | Intake extracts who the concern is about, the concern, duration, and severity cues. Up to 2 follow-up questions are asked when fields are missing. |
| 3 | **Safety check** | Deterministic red-flag rules (pure Python, no LLM) scan the text using phrase lists and negation-aware synonym groups. Highest tier wins. **Emergency exits here with no service search and no LLM call.** |
| 4 | **Find** | The location is resolved against `data/hospitals_clean.csv` via `locations.resolve_location`. If no hospitals match the ZIP, the app widens to city → county → state and notifies the user. |
| 5 | **Compare** | Candidates are scored on type fit, trauma level, straight-line distance (Haversine), and bed count. Urgent cases weight distance more heavily. Top 3 recommendations plus one fallback are selected. |
| 6 | **Final check** | Every hospital ID, name, and phone in the answer is verified against `data/hospitals_clean.csv`. The validation year must appear. A "call ahead to confirm" line must be present. Banned diagnosis/treatment phrases are scanned. Urgent answers must contain urgent-action language. On failure the answer is revised and re-checked — at most 2 retries, then a human-review notice is shown. |
| 7 | **Present** | The recommendation is displayed: hospital card, distance estimate, phone, validation year, and reason. The dataset has no emergency-department field and no opening-hours field — users are directed to call ahead. |
| 8 | **Handoff** | Only after the user clicks one tier-specific button (emergency → **Call 911**, urgent → **Get Directions**, routine → **Create Summary**) does the action proceed. No summary is ever produced without explicit user consent. |

> **Demo path:** set `DATA_SOURCE=demo` to fall back to the original `data/services.json` synthetic directory (used by the test suite).

---

## Project layout

```
carecompass-mvp/
├── app.py                  Streamlit chat UI (State, County, City, ZIP picker)
├── requirements.txt
├── .env.example            Template for WATSONX_* and EMERGENCY_* env vars
├── conftest.py             Adds src/ to sys.path for pytest
├── AGENTS.md               Agent-mode coding rules
│
├── data/
│   ├── hospitals_clean.csv Real US hospitals (generated by scripts/prepare_hospitals.py)
│   ├── services.json       20 synthetic services across 5 demo areas (DATA_SOURCE=demo)
│   ├── red_flags.json      11 red-flag rules (RF001–RF011)
│   ├── messages.json       Verbatim emergency/urgent/review messages
│   ├── scenarios.json      47 test scenarios (16 emergency, 17 urgent, 14 routine)
│   ├── banned_phrases.json Diagnosis and treatment phrases the app must never produce
│   ├── care_levels.json    Keyword → service-type preference mapping
│   └── area_centres.json   Approximate centre coordinates per demo area
│
├── src/
│   ├── safety.py           Red-flag phrase matching — deterministic, no LLM
│   ├── intake.py           Case extraction — offline + optional watsonx.ai
│   ├── finder.py           Service filtering and distance estimation
│   ├── locations.py        State/county/city/ZIP lookups against hospitals_clean.csv
│   ├── advisor.py          Scoring, ranking, and reason building
│   ├── checks.py           Final-check validation (13 sub-checks: 7 hospitals_us + 6 demo, shared banned-phrase and urgent-advice checks always run)
│   ├── orchestrator.py     8-step state machine; emergency exit; consent gate
│   └── audit.py            JSONL audit log writer (logs/audit.jsonl)
│
├── scripts/
│   └── prepare_hospitals.py  Converts data/raw/Hospitals.csv → data/hospitals_clean.csv
├── tests/                  pytest suite (fully offline)
├── docs/
│   ├── SPEC.md             Original technical specification
│   ├── USER_GUIDE.md       End-user walkthrough
│   ├── ARCHITECTURE.md     Component design and data flows
│   ├── GOVERNANCE.md       Safety rules, consent gate, banned phrases, testing strategy
│   ├── TESTING.md          Test suite reference
│   └── DATA.md             Data dictionary for hospitals_clean.csv
└── logs/                   audit.jsonl and benchmark.jsonl written here at runtime
```

---

## Data files

| File | Contents |
|---|---|
| `data/hospitals_clean.csv` | US hospitals from the Kaggle dataset (see below). Generated by `scripts/prepare_hospitals.py`. Not committed — must be generated locally. |
| `data/services.json` | 20 synthetic services (4 per area) used when `DATA_SOURCE=demo`. Types: `pharmacy`, `primary_care`, `hospital_emergency`, `specialist`, `telemedicine`. All names, phones, and addresses are invented. |
| `data/red_flags.json` | 11 red-flag rules (RF001–RF011). 8 emergency, 3 urgent. Each rule has `phrases` (substring match) and/or `term_groups` (conjunction of synonym groups, negation-aware). |
| `data/messages.json` | Verbatim messages for emergency, urgent, needs-human-review, and handoff prompts. `{emergency_number}` and `{emergency_region}` are replaced at runtime. |
| `data/scenarios.json` | 47 test scenarios with `expected_tier`, `expected_red_flag_id`, and `acceptable_service_types`. Used by the benchmark. |
| `data/banned_phrases.json` | 3 categories (BP001 diagnosis, BP002 treatment/dosage, BP003 condition naming). `final_check` blocks any answer containing these phrases. |
| `data/care_levels.json` | Keyword → preferred service types list, used by `finder.map_to_service_types()`. |
| `data/area_centres.json` | Lat/lon centre of each demo area, used for Haversine distance estimation when `DATA_SOURCE=demo`. |

### US hospitals dataset

- **Source:** Kaggle — [US Hospitals by andrewmvd](https://www.kaggle.com/datasets/andrewmvd/us-hospitals)
- **Licence:** Consult the Kaggle dataset page for applicable licence terms before use.
- **Validation years:** `VAL_DATE` values in the cleaned file range from **2013 to 2022**.
  Phone numbers and listed services may be out of date.
- **No emergency-department field:** the dataset does not indicate whether a facility has
  an ED. Facility-type filtering (`GENERAL ACUTE CARE`, `CRITICAL ACCESS`, etc.) is the
  only proxy.
- **No opening-hours field:** the app cannot determine whether a facility is currently
  open. Users must call ahead.
- **US-only:** coverage is limited to US facilities.

### Regenerating hospitals_clean.csv

1. Download `Hospitals.csv` from the Kaggle page above and place it at
   `data/raw/Hospitals.csv`.
2. Run:
   ```powershell
   python scripts/prepare_hospitals.py
   ```
   The script reads `data/raw/Hospitals.csv`, applies status, coordinate, and
   duplicate filters, normalises columns, and writes `data/hospitals_clean.csv`.
   It prints a summary report on completion.

---

## Known limits of this MVP

- **Safety detection is keyword-based.** Indirect descriptions of urgent conditions (e.g.
  "my asthma inhaler stopped working") may not escalate without the watsonx.ai intake step.
- **Hospital data may be out of date.** Records were validated between 2013 and 2022.
  Phone numbers, facility names, and listed services may have changed. Always call ahead.
- **No emergency-department field.** The dataset does not record whether a facility has an
  ED. The app uses facility type (`GENERAL ACUTE CARE`, `CRITICAL ACCESS`) as a proxy.
- **No opening-hours field.** The app cannot determine whether a facility is currently
  open. Users must call ahead to confirm.
- **Distance is straight-line (Haversine) from the selected location's mean centre,** not
  door-to-door routing. Travel time estimates assume 40 mph average speed.
- **Location widening.** If no hospitals are found for a ZIP code, the app automatically
  widens to city, then county, then state level. A notice is shown when this happens.
- **Follow-up questions are generated but not yet surfaced in the UI.** The orchestrator
  detects missing fields and produces a question, but `app.py` does not display it before
  running the pipeline.
- **The final-check revision is cosmetic.** Appending a disclaimer and retrying passes the
  urgent-advice check but does not fix structural problems such as a missing phone number.
- **`DATA_SOURCE=demo` falls back to the synthetic directory** (`data/services.json`).
  This mode is used by the existing test suite and keeps the demo areas available.

---

## Security and deployment

> **This application is designed for local demonstration only.**
> Running it on a network-accessible server requires additional hardening steps.

Key points:

- **Audit logs contain no typed health text by default.**  `LOG_USER_TEXT=false` (the
  default) means the log records only message length, a short hash, tier, step names, and
  timings.  Phone numbers are always redacted.  Raw health text is never written unless
  `LOG_USER_TEXT=true` is explicitly set (development/walkthrough only).

- **Before any deployment**, complete the checklist in
  [`docs/SECURITY.md`](docs/SECURITY.md): add a reverse proxy with per-IP rate limiting,
  terminate TLS, set `server.headless = true`, redirect stderr to an internal log sink,
  run `pip-audit`, and enforce the 90-day log-retention schedule.

See [`docs/SECURITY.md`](docs/SECURITY.md) for the full threat model, the list of
controls in place, and each finding with its status.

---

## Docs

- [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) — step-by-step guide for end users
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — component design, scoring weights, data flows
- [`docs/GOVERNANCE.md`](docs/GOVERNANCE.md) — safety rules, consent gate, banned phrases, testing
- [`docs/SECURITY.md`](docs/SECURITY.md) — threat model, controls, and security findings
- [`docs/TESTING.md`](docs/TESTING.md) — test suite reference
- [`docs/DATA.md`](docs/DATA.md) — data dictionary for hospitals_clean.csv
- [`docs/SPEC.md`](docs/SPEC.md) — original technical specification
