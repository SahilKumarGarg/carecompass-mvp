# CareCompass — Architecture

> **Demo application.** All data is synthetic. Not for clinical use.

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
        ├─ 4. find   finder.find_services()
        │         ├─ map concern → preferred service types  (care_levels.json)
        │         ├─ filter services.json by area + type
        │         └─ compute haversine distance from area_centres.json
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

### [`src/finder.py`](../src/finder.py)

1. **`map_to_service_types(concern, tier, safety_hits)`** — maps the concern string and
   safety rule hits to an ordered list of preferred service types using keywords from
   `data/care_levels.json`. Service-type hints on red-flag rules (e.g. RF008 →
   `hospital_emergency`, RF009 → `specialist`) are promoted to the front of the list.
2. **`find_services(case, tier, safety_hits)`** — filters `data/services.json` by area and
   type, computes Haversine distance from `data/area_centres.json`, and returns
   `ServiceCandidate` objects.

Haversine formula provides straight-line distance in km. Travel time is estimated at
25 km/h (walking/slow traffic pace) and explicitly labelled as an estimate in the UI.

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

## Demo services (20 total, 4 per area)

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
| `EMERGENCY_NUMBER` | Number shown in emergency/urgent messages | `[DEMO-999]` |
| `EMERGENCY_REGION` | Region label in urgent body message | `DEMO REGION` |

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
