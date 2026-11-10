# CareCompass — Architecture

> **Not for clinical use.** This application uses a public US hospitals dataset
> (Kaggle — [US Hospitals by andrewmvd](https://www.kaggle.com/datasets/andrewmvd/us-hospitals)).
> Records were validated between 2013 and 2022; phone numbers and services may be out of
> date. The dataset has no emergency-department field and no opening-hours field.

---

## Overview

CareCompass is a single-server Python application. The UI is built with Streamlit;
the backend is a pure-Python pipeline with no database, no message queue, and no
external dependencies beyond an optional watsonx.ai call. The entire emergency path
runs with zero network access.

```
Browser (Streamlit)
        │ user_text + area
        ▼
   orchestrator.run_session()          ← 8-step state machine
        │
        ├─ 1. describe  (log)
        ├─ 2. understand  intake.extract_case()
        │         ├─ offline: regex / keyword extraction
        │         └─ [optional] watsonx.ai granite-13b-instruct-v2
        │
        ├─ 3. safety  safety.check_red_flags()          ← PURE PYTHON, NO LLM
        │         ├─ emergency → build answer → RETURN (steps 4-8 skipped)
        │         └─ urgent / routine → continue
        │
        ├─ 4. find   finder.find_hospitals() [hospitals_us] / finder.find_services() [demo]
        │         ├─ [hospitals_us] locations.resolve_location() → candidate hospital rows
        │         │     └─ widens ZIP→city→county→state if needed; notice returned
        │         ├─ [hospitals_us] filter by facility type for case (type_fit 3/2/1)
        │         ├─ [demo] map concern → preferred service types  (care_levels.json)
        │         ├─ [demo] filter services.json by area + type
        │         └─ compute haversine distance (miles from ref point [hospitals_us] /
        │                                        km from area centre [demo])
        │
        ├─ 5. compare  advisor.rank()
        │         └─ score each candidate (fit + distance + open + freshness)
        │            → top 3 + fallback (4 total)
        │
        ├─ 6. final_check  checks.final_check()   (≤ 2 retries)
        │         ├─ check service IDs exist in services.json
        │         ├─ check service names appear in answer
        │         ├─ check phone numbers appear in answer
        │         ├─ check no banned diagnosis/treatment phrases
        │         ├─ check last_verified date appears (or stale warning)
        │         └─ check urgent-action language present (when tier=urgent)
        │
        ├─ 7. present  build answer text  → SessionResult
        │
        └─ 8. handoff  create_handoff_summary()   ← ONLY after explicit consent
                  └─ called by app.py when user clicks "Yes"
```

---

## Modules

### [`src/orchestrator.py`](../src/orchestrator.py)

The central state machine. Calls every other module in order and returns a
[`SessionResult`](../src/orchestrator.py) dataclass with all intermediate outputs.

Key design rules:
- **Emergency exit is the first branch after `check_red_flags()`**, before any LLM call.
  Adding code between the safety check and the emergency return breaks the offline guarantee.
- `_MAX_CHECK_RETRIES = 2` — `final_check` is called at most 3 times (original + 2 retries).
  Do not increase this limit.
- `create_handoff_summary()` is a separate public function that must be called explicitly.
  `run_session()` never populates `session_result.handoff_summary`.

### [`src/safety.py`](../src/safety.py)

Deterministic red-flag scanning. Reads `data/red_flags.json` at module load time.

**Normalisation** (`_normalise`): lowercase → strip Unicode accents → replace all
punctuation with a space → collapse whitespace. No spelling correction.

**Two matching strategies per rule, both independent:**

| Strategy | Mechanics |
|---|---|
| `phrases` | Any single phrase (after normalisation) is a substring of the normalised text → rule fires. No negation check. Safe-side: use for unambiguous strings like `"chest pain"`. |
| `term_groups` | Every group must have ≥1 synonym that is (a) present in the text and (b) not immediately preceded by a negation word within a 3-token window. All groups matching → rule fires. |

**Negation window:** 3 tokens before the synonym. Negators: `no`, `not`, `without`,
`never`, `dont`, `doesnt`, `didnt`, `neither`, `nor`.

**Tier precedence:** highest tier across all matching rules wins.
`emergency > urgent > routine`.

### [`src/intake.py`](../src/intake.py)

Extracts a structured [`CaseResult`](../src/intake.py) from free-form text.

| Mode | Trigger | Fields extracted |
|---|---|---|
| **Offline** | watsonx.ai env vars absent or invalid | Regex/keyword extraction of who, concern, duration, severity cues, denied symptoms |
| **LLM** | `WATSONX_API_KEY`, `WATSONX_URL`, `WATSONX_PROJECT_ID` all set | Granite model JSON response; falls back to offline on any error |

Concern budget: 200 characters of the user's own words (duration words preserved — W8-5).
Up to 2 follow-up questions are generated for missing fields (`who`, `concern`, `duration`).

### [`src/locations.py`](../src/locations.py)

Geographic lookups against `data/hospitals_clean.csv`. Used by the `hospitals_us` path.

- **`list_states()`** — sorted list of two-letter state codes in the dataset (used by the
  State dropdown).
- **`list_counties(state)`** — sorted `(county_name, countyfips)` pairs for a state.
- **`list_cities(state, county)`** — sorted city names, optionally scoped to a county.
- **`resolve_location(state, county, city, zip)`** — returns a `LocationResult` containing
  matching hospital rows and a reference point (mean lat/lon). Resolves at the most
  specific level supplied and automatically widens (ZIP → city → county → state) when no
  rows are found. Widening is never silent: `notice` carries a human-readable explanation.
  Raises `ValueError` when no hospitals match even at the broadest level.

State is always required in every sub-query to prevent mixing hospitals from different
states that share a city or county name.

### [`src/finder.py`](../src/finder.py)

The module checks `DATA_SOURCE` (env var) at call time:

| `DATA_SOURCE` | Path used |
|---|---|
| `hospitals_us` (default) | `data/hospitals_clean.csv` via `find_hospitals()` |
| `demo` | `data/services.json` via `find_services()` |

**`hospitals_us` path — `find_hospitals(ref_lat, ref_lon, case, tier, candidate_rows)`**

Receives pre-filtered hospital rows from `locations.resolve_location()`, applies type
filtering (excludes `MILITARY`, `LONG TERM CARE`, `REHABILITATION`, `CHRONIC DISEASE`,
`SPECIAL`), and returns `HospitalCandidate` objects. Type fit is scored 3/2/1:

| Concern type | Primary (3) | Secondary (2) |
|---|---|---|
| Mental health / self-harm | `PSYCHIATRIC` | `GENERAL ACUTE CARE` |
| Child patient | `CHILDREN` | `GENERAL ACUTE CARE` |
| Pregnancy / obstetric | `GENERAL ACUTE CARE` | `WOMEN` |
| Emergency / urgent / routine | `GENERAL ACUTE CARE` | `CRITICAL ACCESS` |

Distance is in **miles** at 40 mph estimated travel speed. `val_year` (the 4-digit
validation year from `VAL_DATE`) is always carried on the candidate.

**`demo` path — `find_services(case, tier, safety_hits)`**

Filters `data/services.json` by area and type; computes Haversine distance in **km** at
25 km/h from `data/area_centres.json`; returns `ServiceCandidate` objects. Service-type
hints on red-flag rules (e.g. RF008 → `hospital_emergency`, RF009 → `specialist`) are
promoted to the front of the preferred-types list.

### [`src/advisor.py`](../src/advisor.py)

Scores each `ServiceCandidate` on four dimensions and returns up to 4 `RankedResult`
objects (positions 1–3 are recommendations; position 4 `is_fallback=True` is the fallback).

**Scoring weights:**

| Dimension | Routine | Urgent | Notes |
|---|---|---|---|
| Type fit (0–1) | 40 % | 25 % | `fit_score / 3` where fit_score is 0–3 from finder |
| Distance (0–1) | 20 % | 35 % | `1 − (km / 10)`; telemedicine = 1.0 |
| Open-24h (0–1) | 20 % | 30 % | `open_24h` → 1.0; otherwise 0.5 |
| Freshness (0–1) | 20 % | 10 % | Linear decay over 90 days; −0.3 penalty if > 60 days old |

Reason text is built from record fields only (name, distance, hours, services_offered,
last_verified, phone). An optional LLM reword pass uses the same Granite model.

### [`src/checks.py`](../src/checks.py)

Six sequential sub-checks, all returning a `problems: list[str]`:

| # | Check | Failure condition |
|---|---|---|
| 1 | Service IDs | Any cited ID not in `services.json` |
| 2 | Service names | Any cited ID whose name does not appear in the answer text |
| 3 | Phone numbers | Any cited ID whose phone digits do not appear in the answer |
| 4 | Banned phrases | Any phrase from `banned_phrases.json` found (case-insensitive, normalised) |
| 5 | Last-verified date | Date string absent and no stale "call ahead" warning present |
| 6 | Urgent-action language | Tier is `urgent` and none of the `_URGENT_ADVICE_MARKERS` appear |

`_banned_cache` and `_services_index` are module-level caches — loaded once at import,
never reloaded. This is intentional (consistent with the project data-loading pattern).

### [`src/audit.py`](../src/audit.py)

Appends one JSON line per event to `logs/audit.jsonl`. Each line has:

```json
{"ts": "2026-11-03T17:00:00", "session_id": "...", "step": "safety", "data": {...}}
```

The benchmark writes its per-scenario records to a separate file `logs/benchmark.jsonl`
via `audit.log_benchmark()` so that routine audit logs and benchmark runs stay separate.

---

## Data model

### `SessionResult` (returned by `run_session`)

| Field | Type | Set on emergency? | Notes |
|---|---|---|---|
| `session_id` | `str` | ✅ | UUID assigned at start |
| `tier` | `str` | ✅ | `"emergency"` \| `"urgent"` \| `"routine"` |
| `state` | `str` | ✅ | `"emergency"` \| `"present"` \| `"needs_human_review"` |
| `case` | `CaseResult` | ✅ | Intake result (even on emergency) |
| `safety` | `SafetyResult` | ✅ | Tier + list of `RuleHit` |
| `candidates` | `list[ServiceCandidate]` | `[]` | Empty on emergency |
| `ranked` | `list[RankedResult]` | `[]` | Empty on emergency |
| `answer_text` | `str` | ✅ | Full text shown to user |
| `check_passed` | `bool` | `True` | Emergency bypasses `final_check` |
| `needs_human_review` | `bool` | `False` | True only when retries exhausted |
| `handoff_summary` | `Optional[str]` | `None` | Only set by `create_handoff_summary()` |
| `followup_questions_asked` | `int` | `0` | 0 or 1 |
| `missing_fields` | `list[str]` | from intake | Fields that could not be extracted |

### `RuleHit` (inside `SafetyResult.hits`)

```python
RuleHit(rule_id="RF003", name="stroke_signs", tier="emergency",
        matched_phrase="face drooping")
```

### `ServiceCandidate` (from finder)

Extends the raw service JSON fields with computed `distance_km`, `travel_min_estimate`,
and `fit_score`.

---

## Red-flag rules (RF001–RF011)

| ID | Name | Tier | Service-type hint |
|---|---|---|---|
| RF001 | chest_pain | emergency | — |
| RF002 | trouble_breathing | emergency | — |
| RF003 | stroke_signs | emergency | — |
| RF004 | severe_bleeding | emergency | — |
| RF005 | unconscious | emergency | — |
| RF006 | seizure | emergency | — |
| RF007 | severe_allergic_reaction | emergency | — |
| RF008 | confusion_with_fever | **urgent** | `hospital_emergency` |
| RF009 | self_harm_thoughts | **urgent** | `specialist` |
| RF010 | pregnancy_emergency | emergency | — |
| RF011 | fever_in_child | **urgent** | `primary_care` |

Rules with `service_type` hints have that type promoted to the front of the finder's
preferred-type list so the service search is biased toward the right kind of care.

---

## Dataset summary (hospitals_us path)

The active dataset is `data/hospitals_clean.csv`, generated from the Kaggle US Hospitals
dataset by `scripts/prepare_hospitals.py`.

**Key limitations carried through to the UI:**

| Limitation | Detail |
|---|---|
| Validation years | `VAL_DATE` ranges from **2013 to 2022**; some records will be outdated |
| No ED field | The source has no `has_emergency_department` column; `TYPE` is used as proxy |
| No opening-hours field | Hours of operation are not in the source; users must call ahead |
| US-only | Coverage limited to US facilities; overseas territories may be incomplete |
| Demo/test mode | `DATA_SOURCE=demo` uses `data/services.json` (20 synthetic services, 5 areas) |

See [`docs/DATA.md`](DATA.md) for the full column dictionary and all cleaning rules applied
by the preparation script.

## Demo services (`DATA_SOURCE=demo`, 20 total, 4–5 per area)

The demo directory (`data/services.json`) is used when `DATA_SOURCE=demo` and by the
existing test suite. All names, phones, and addresses are **invented**.

| Area | ID | Name | Type | 24h |
|---|---|---|---|---|
| Greenfield | SVC001 | Greenfield Community Pharmacy | pharmacy | No |
| Greenfield | SVC002 | Greenfield Family Practice | primary_care | No |
| Greenfield | SVC003 | Greenfield Urgent Treatment Centre | hospital_emergency | No |
| Greenfield | SVC004 | Greenfield Diabetes & Endocrine Clinic | specialist | No |
| Greenfield | SVC005 | CareNow Telemedicine | telemedicine | No |
| Riverside | SVC006 | Riverside 24-Hour Pharmacy | pharmacy | **Yes** |
| Riverside | SVC007 | Riverside Medical Centre | primary_care | No |
| Riverside | SVC008 | St Agatha's Hospital A&E | hospital_emergency | **Yes** |
| Riverside | SVC009 | Riverside Cardiology Outpatients | specialist | No |
| Riverside | SVC010 | HealthLink Video GP | telemedicine | No |
| Northgate | SVC011 | Northgate Late-Night Pharmacy | pharmacy | No |
| Northgate | SVC012 | Northgate Health Hub | primary_care | No |
| Northgate | SVC013 | Northgate Walk-In Centre | hospital_emergency | No |
| Northgate | SVC014 | Northgate Mental Health Team | specialist | No |
| Northgate | SVC015 | MyDoc Online Clinic | telemedicine | No |
| Eastfield | SVC016 | Eastfield Boots Pharmacy | pharmacy | No |
| Eastfield | SVC017 | Eastfield Surgery | primary_care | No |
| Eastfield | SVC018 | Eastfield Hospital A&E | hospital_emergency | **Yes** |
| Westbury | SVC019 | Westbury Community Pharmacy | pharmacy | No |
| Westbury | SVC020 | Westbury Primary Care Network | primary_care | No |

> Eastfield has no specialist or telemedicine service. Westbury has no hospital_emergency.
> The finder falls back to the next preferred type in those cases.

---

## Emergency path: offline guarantee

The emergency path is guaranteed to work with no LLM and no network. The sequence is:

```
run_session(user_text, area)
  → extract_case()          [offline fallback always available]
  → check_red_flags()       [pure Python, reads local JSON only]
  → tier == "emergency"?
      YES → load messages.json → fill placeholders → return SessionResult
                                  [NO finder, NO advisor, NO final_check called]
```

`check_passed=True` is set unconditionally on the emergency return because the verbatim
emergency message cannot fail the service-grounding checks (it cites no service).

Tests in [`tests/test_safety_proof.py`](../tests/test_safety_proof.py) — class
`TestOfflineEmergency` — verify all 7 emergency rule families fire correctly in offline
mode, that `candidates` and `ranked` are empty, and that the answer contains the
configured emergency number without any banned phrases.

---

## Configuration

All configuration is via environment variables read at runtime from `.env`:

| Variable | Purpose | Default if absent |
|---|---|---|
| `WATSONX_API_KEY` | watsonx.ai authentication | (blank) → offline mode |
| `WATSONX_URL` | watsonx.ai service URL | (blank) → offline mode |
| `WATSONX_PROJECT_ID` | watsonx.ai project | (blank) → offline mode |
| `EMERGENCY_NUMBER` | Number shown in emergency/urgent messages | `911` |
| `EMERGENCY_REGION` | Region label in urgent body message | `US` |
| `DATA_SOURCE` | `hospitals_us` (real dataset) or `demo` (synthetic services) | `hospitals_us` |

`_emergency_number()` and `_emergency_region()` in `orchestrator.py` strip whitespace
and fall back to the demo defaults for blank strings as well as absent variables.

---

## Audit log format

Every event appended to `logs/audit.jsonl`:

```json
{"ts": "2026-11-03T17:00:00.123456", "session_id": "abc123", "step": "safety",
 "data": {"tier": "urgent", "hits": [{"rule_id": "RF008", ...}]}}
```

Steps in order: `describe`, `understand`, `followup`?, `safety`, `emergency_exit`?
(emergency) or `find`, `compare`, `final_check` (×1–3), `revise`?, `needs_human_review`?,
`present`, `handoff`? (only with consent).

Benchmark events go to `logs/benchmark.jsonl` (separate file, written by `audit.log_benchmark()`).
