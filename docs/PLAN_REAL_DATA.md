# PLAN_REAL_DATA.md — Replace Synthetic Service Directory with US Hospitals CSV

## Overview

Replace the 20-record synthetic `data/services.json` with the 8,013-row US
Hospitals CSV (`data/raw/Hospitals.csv`) while keeping the full demo-data path
working unchanged. Introduce a `DATA_SOURCE` setting (`hospitals_us` or `demo`)
that selects which backend loads at runtime. The 5-area dropdown becomes a
US-state picker (or state+city pair). All existing safety, benchmark, and
check tests remain green; new tests cover the CSV ingest and the real-data
finder path.

---

## Part 1 — Affected Inventory

### Source files that reference `services.json`, SVC IDs, areas, or the area dropdown

| File | What it references | How it uses it |
|------|--------------------|----------------|
| `src/finder.py` | `_SERVICES_PATH`, `_AREA_CENTRES_PATH`, `area` field on every service | Loads the list, filters by `svc["area"]`, computes haversine from `area_centres.json` |
| `src/checks.py` | `_SERVICES_PATH` | Builds a service index keyed by `id`; validates IDs, names, phones, `last_verified` |
| `src/orchestrator.py` | SVC IDs in `_build_answer_text`, `_build_handoff_summary` | Embeds `svc.id`, `svc.phone`, `svc.last_verified` in answer text |
| `app.py` | `_AREAS` list, `st.selectbox("Your area", _AREAS, …)` | Hard-codes the 5 demo area names; passes selected value to `run_session` |
| `src/intake.py` | `area: str` field on `CaseResult` | Carries area through pipeline; no area-list validation |
| `scripts/refresh_dates.py` | `data/services.json` directly | Rewrites `last_verified` dates in the JSON file |

### Test files that reference `services.json`, SVC IDs, or areas

| Test file | What it touches |
|-----------|-----------------|
| `tests/test_finder.py` | `find_services` with named areas ("Greenfield", "Riverside", "Northgate", "Eastfield"); asserts `c.id.startswith("SVC")`; checks `c.area == "Riverside"` |
| `tests/test_checks.py` | Loads `data/services.json` directly; uses hard-coded `SVC001`, `SVC002`, `SVC003` IDs, names, phones, dates |
| `tests/test_advisor.py` | Builds `ServiceCandidate` fixtures with demo SVC IDs and demo dates |
| `tests/test_orchestrator.py` | Calls `run_session(…, area="Greenfield")` and similar demo areas |
| `tests/test_benchmark.py` | Runs all 17 scenarios; scenarios carry `area` and `expected_service_type` from `data/scenarios.json` |
| `tests/test_safety_proof.py` | Loads `services.json` to build test answer strings |
| `tests/test_banned_phrases.py` | Builds answer strings using SVC IDs from `services.json` |

### Data files that reference areas or services

| File | Role |
|------|------|
| `data/services.json` | The synthetic service list (20 records, SVC001–SVC020) |
| `data/area_centres.json` | Haversine anchor point per demo area |
| `data/scenarios.json` | 17 test scenarios each with an `area` field (demo names) and `expected_service_type` |
| `data/care_levels.json` | Keyword→service-type mappings (area-independent; unchanged) |

### Docs

| File | References |
|------|------------|
| `docs/SPEC.md` | Mentions "5 demo areas", `data/services.json` in data-files section |
| `docs/user_test/walkthrough_output.md` | References demo area names in session output |

---

## Part 2 — New Data Flow

```
data/raw/Hospitals.csv
        │
        ▼
[Step A] scripts/build_hospitals_db.py
        │  • reads CSV, drops CLOSED rows
        │  • normalises "NOT AVAILABLE" / -999 → null
        │  • renames columns to match ServiceCandidate schema
        │  • maps hospital TYPE → internal service type
        │  • writes data/hospitals_us.json  (list of dicts, same shape as services.json entries)
        │
        ▼
data/hospitals_us.json          data/services.json  (unchanged)
        │                               │
        └──────── DATA_SOURCE ──────────┘
                       │
                       ▼
              src/service_loader.py   (NEW)
                       │  • reads DATA_SOURCE from env (default "demo")
                       │  • returns list[dict] — same schema either way
                       │
                       ▼
              src/finder.py           (MODIFIED)
                       │  • calls service_loader instead of reading services.json directly
                       │  • area filter: demo path uses svc["area"]; hospitals_us path
                       │    uses STATE (and optionally CITY) with a user-supplied value
                       │
                       ▼
              src/location_resolver.py  (NEW)
                       │  • given a US state abbreviation (e.g. "TX") returns a
                       │    dict{"lat", "lon"} for haversine anchor
                       │  • reads data/state_centres.json
                       │
                       ▼
              src/advisor.py          (UNCHANGED — uses ServiceCandidate fields only)
                       │
                       ▼
              src/checks.py           (MODIFIED)
                       │  • calls service_loader to rebuild its index
                       │  • last_verified check: accepts both ISO date and
                       │    "VAL_DATE not available" sentinel gracefully
                       │
                       ▼
              app.py                  (MODIFIED)
                       │  • DATA_SOURCE=hospitals_us: dropdown shows US state names
                       │  • DATA_SOURCE=demo: dropdown shows 5 demo areas (unchanged)
```

### Schema mapping — CSV column → ServiceCandidate field

| CSV column | ServiceCandidate field | Notes |
|------------|------------------------|-------|
| `ID` | `id` | Prefix with `HSP-` to distinguish from SVC IDs |
| `NAME` | `name` | Strip leading/trailing whitespace |
| `TELEPHONE` | `phone` | "NOT AVAILABLE" → empty string |
| `TYPE` | `type` | Map to internal type (see type-mapping table below) |
| `STATE` | `area` | Used as the area filter key in hospitals_us mode |
| `LATITUDE` | `lat` | -999 → null; float |
| `LONGITUDE` | `lon` | -999 → null; float |
| `VAL_DATE` | `last_verified` | Parse `YYYY/MM/DD HH:MM:SS+00` → ISO `YYYY-MM-DD`; "NOT AVAILABLE" → null |
| `STATUS` | *(filter)* | Only OPEN rows are kept |
| `TRAUMA` | `services_offered` | Include trauma level if not "NOT AVAILABLE" |
| `HELIPAD` | `services_offered` | Append "helipad available" if Y |
| `BEDS` | *(capacity hint)* | Stored but not exposed in ServiceCandidate yet |
| `WEBSITE` | *(ignored for now)* | Not in ServiceCandidate schema |
| `OWNER` | `cost_note` | "GOVERNMENT" → "Public hospital"; "NON-PROFIT" → "Non-profit"; "PROPRIETARY" → "Private" |
| *(derived)* | `hours` | Hard-coded sentinel: "Hours not available — call ahead" |
| *(derived)* | `open_24h` | False (unknown); hospital_emergency type sets True |
| *(derived)* | `languages` | `["English"]` (unknown from CSV) |
| *(derived)* | `source` | `"hospitals_us_csv"` |

### Hospital TYPE → internal service type

| CSV TYPE value | Internal service type |
|----------------|----------------------|
| `GENERAL ACUTE CARE` | `hospital_emergency` |
| `CRITICAL ACCESS` | `hospital_emergency` |
| `PSYCHIATRIC` | `specialist` |
| `REHABILITATION` | `specialist` |
| `LONG TERM CARE` | `specialist` |
| `SPECIAL` | `specialist` |
| `CHILDREN` | `hospital_emergency` |
| *(anything else)* | `hospital_emergency` |

---

## Part 3 — DATA_SOURCE Setting

`DATA_SOURCE` is read from `.env` / environment variables, with `demo` as the
default. The value is consumed **only** in `src/service_loader.py`.

```
DATA_SOURCE=demo          → load data/services.json (current synthetic data)
DATA_SOURCE=hospitals_us  → load data/hospitals_us.json (cleaned CSV data)
```

### Why a loader module rather than a flag in finder.py

`checks.py` also needs to load the service list independently of `finder.py`.
A shared `service_loader.py` keeps the switching logic in one place and avoids
the two consumers going out of sync.

### Backward compatibility guarantee

- `DATA_SOURCE=demo` (or absent) behaves **identically** to the current code.
- All 17 benchmark scenarios, all existing unit tests, and the demo UI continue
  to pass without any change to test data or test logic.
- The `demo` backend still reads `data/services.json`; the area dropdown still
  shows the 5 demo area names.

---

## Part 4 — Build Order

Each step is independently testable and reviewable before the next begins.

---

### Step 1 — CSV ingest script and cleaned JSON

**Intent:** Produce a clean, machine-readable `data/hospitals_us.json` from the
raw CSV. This is the only place where CSV-specific logic lives.

**Expected outcomes:**
- `data/hospitals_us.json` exists and contains only OPEN hospitals.
- Every record has the same keys as a `services.json` entry (plus `state`,
  `city`, `county`, `beds`).
- "NOT AVAILABLE" and -999 are normalised to `null`.
- `VAL_DATE` is parsed to ISO format where possible.
- `hospital_emergency` type entries have `open_24h: true`.
- A summary line is printed: total rows, OPEN kept, CLOSED dropped, rows with
  null lat/lon, rows with null phone.

**Todo list:**
1. Write `scripts/build_hospitals_db.py`.
2. Add `"demo_data": false` and `"source": "hospitals_us_csv"` top-level keys.
3. Write `tests/test_build_hospitals_db.py` with at least:
   - CLOSED rows are excluded.
   - "NOT AVAILABLE" phone → empty string (or null).
   - -999 lat/lon → null.
   - VAL_DATE parsed correctly.
   - TYPE → service type mapping covers all known values.
   - Output JSON passes a schema check (required keys present on every record).
4. Run the script against the real CSV; confirm row count is plausible (~7,500+
   OPEN rows expected).

**Relevant context:**
- `data/raw/Hospitals.csv` — header row confirmed in CSV section above.
- No new libraries; pandas is NOT assumed present — use `csv` stdlib module.

---

### Step 2 — `src/service_loader.py` and `data/state_centres.json`

**Intent:** Introduce the single switching point for `DATA_SOURCE` and add
US-state anchor coordinates for haversine distance.

**Expected outcomes:**
- `service_loader.load_services() → list[dict]` returns the correct list
  depending on `DATA_SOURCE`.
- `data/state_centres.json` has a `lat/lon` entry for all 50 states + DC.
- `src/location_resolver.resolve(area: str) → dict | None` returns the anchor
  for demo areas (from `area_centres.json`) or US states (from
  `state_centres.json`), unified by `DATA_SOURCE`.

**Todo list:**
1. Write `data/state_centres.json` (approximate geographic centres for all
   50 states + DC; sourced from public-domain reference data).
2. Write `src/service_loader.py` with `load_services()`.
3. Write `src/location_resolver.py` with `resolve(area)`.
4. Write `tests/test_service_loader.py`:
   - `DATA_SOURCE=demo` returns a list where every `id` starts with `SVC`.
   - `DATA_SOURCE=hospitals_us` returns a list where no `id` starts with `SVC`.
   - Unknown `DATA_SOURCE` raises `ValueError`.
5. Write `tests/test_location_resolver.py`:
   - Known demo area returns same coords as current `area_centres.json`.
   - Known US state returns non-null coords.
   - Unknown area returns `None`.

**Relevant context:**
- `data/area_centres.json` — current anchor format: `{"lat": …, "lon": …}`.
- `_load_area_centres()` in `src/finder.py` — this will be replaced by
  `location_resolver.resolve()` in Step 3.

---

### Step 3 — Modify `src/finder.py` to use the loader

**Intent:** Make `finder.py` data-source-agnostic by replacing its direct
`services.json` / `area_centres.json` reads with calls to the new loader and
resolver.

**Expected outcomes:**
- `DATA_SOURCE=demo`: `find_services` behaviour is byte-for-byte identical to
  the current code (all existing `test_finder.py` tests pass unchanged).
- `DATA_SOURCE=hospitals_us`: `find_services` filters by `svc["area"] == STATE`
  (e.g., `"TX"`), computes haversine from the state centre, and returns
  `ServiceCandidate` objects with real data.
- `_load_services()` calls `service_loader.load_services()`.
- `_load_area_centres()` calls `location_resolver.resolve(case.area)`.

**Todo list:**
1. Replace `_SERVICES_PATH` load in `finder.py` with `service_loader.load_services()`.
2. Replace `area_centres.get(case.area)` with `location_resolver.resolve(case.area)`.
3. Clear module-level caches between `DATA_SOURCE` switches (relevant only in
   tests that monkey-patch the env var).
4. Add `tests/test_finder_hospitals.py`:
   - With `DATA_SOURCE=hospitals_us`, `find_services` returns candidates
     whose `area` equals the requested US state.
   - `distance_km` is non-null for hospitals with valid lat/lon.
   - Hospitals with null lat/lon get `distance_km=None`.
   - All returned IDs start with `HSP-`.
5. All existing `tests/test_finder.py` tests still pass with `DATA_SOURCE=demo`.

**Relevant context:**
- `finder.py` lines 66–97 — the four loader functions to be replaced.
- `finder.py` line 224 — `centres.get(case.area)` → replaced by resolver call.
- `finder.py` line 228 — `svc["area"].lower() != case.area.lower()` — logic
  unchanged; works for both state codes and demo area names.

---

### Step 4 — Modify `src/checks.py` to use the loader

**Intent:** Make `checks.py` data-source-agnostic. The six validation checks
remain identical in logic; only the data source for the service index changes.

**Expected outcomes:**
- `_load_services_index()` calls `service_loader.load_services()` instead of
  reading `services.json` directly.
- All existing `test_checks.py` tests pass with `DATA_SOURCE=demo`.
- `last_verified` check handles `null` gracefully (skips the check when no
  date is available — same as the existing `if not date_str: continue` branch).

**Todo list:**
1. Replace `_SERVICES_PATH.open(…)` in `_load_services_index()` with
   `service_loader.load_services()`.
2. Confirm the `if not date_str: continue` branch already handles null —
   it does; no new code needed.
3. All existing `tests/test_checks.py` tests pass unchanged.
4. Add one test in `tests/test_checks_hospitals.py`:
   - With `DATA_SOURCE=hospitals_us`, citing a valid `HSP-` ID passes the
     ID check; citing an unknown ID fails it.

**Relevant context:**
- `checks.py` lines 70–77 — `_load_services_index()` to be changed.
- `checks.py` lines 175–189 — `_check_last_verified()` null guard already present.

---

### Step 5 — Modify `app.py` area dropdown

**Intent:** When `DATA_SOURCE=hospitals_us`, the dropdown shows US state names
(or abbreviations). When `DATA_SOURCE=demo`, it shows the current 5 demo areas.

**Expected outcomes:**
- `DATA_SOURCE=demo` (default): UI is pixel-identical to today.
- `DATA_SOURCE=hospitals_us`: dropdown lists all 50 states + DC, sorted
  alphabetically, with full names (e.g., "Texas") as display labels and
  two-letter codes (e.g., "TX") as values passed to `run_session`.
- The `_AREAS` constant is extracted into a helper that reads `DATA_SOURCE`.

**Todo list:**
1. Add a `_get_areas()` function in `app.py` that returns the correct list
   based on `DATA_SOURCE`.
2. Replace the hard-coded `_AREAS` list with a call to `_get_areas()`.
3. The sidebar label changes from "Your area" to "Your state" when
   `DATA_SOURCE=hospitals_us`.
4. No new Streamlit widgets or layout changes.
5. Manual smoke-test: run the app with each `DATA_SOURCE`; confirm dropdown
   populates correctly.

**Relevant context:**
- `app.py` line 28 — `_AREAS` constant.
- `app.py` lines 202–208 — `st.selectbox` call.

---

### Step 6 — Update `scripts/refresh_dates.py` for dual-source

**Intent:** The date-refresh script currently hard-codes `data/services.json`.
Make it respect `DATA_SOURCE` so it can optionally update `VAL_DATE` fields in
`hospitals_us.json` (or skip, since hospital dates come from the CSV build).

**Expected outcomes:**
- `DATA_SOURCE=demo`: script behaves exactly as today (refreshes
  `last_verified` in `data/services.json`).
- `DATA_SOURCE=hospitals_us`: script prints a message that hospital dates are
  set at build time and exits cleanly without modifying any file.

**Todo list:**
1. Wrap existing logic in a `DATA_SOURCE=demo` guard.
2. Add the early-exit message for `hospitals_us`.
3. Existing `TestRefreshDatesScript` tests in `test_advisor.py` pass unchanged.

**Relevant context:**
- `scripts/refresh_dates.py` — currently reads/writes `data/services.json` directly.

---

### Step 7 — Update `data/scenarios.json` and benchmark

**Intent:** The benchmark scenarios use demo area names. Ensure they still run
correctly when `DATA_SOURCE=demo`, and add a separate small fixture set for
`hospitals_us` integration smoke tests.

**Expected outcomes:**
- All 17 existing benchmark scenarios pass with `DATA_SOURCE=demo` (zero
  under-triage; current pass rate maintained).
- A new `tests/test_integration_hospitals.py` runs 3–5 hand-crafted scenarios
  against `DATA_SOURCE=hospitals_us`; verifies that `find_services` returns at
  least one candidate for a populated US state and that `final_check` passes on
  the resulting answer text.

**Todo list:**
1. Confirm existing `data/scenarios.json` is untouched (demo path only).
2. Write `tests/test_integration_hospitals.py` with 3–5 scenarios:
   - A routine concern in a well-populated state (e.g., TX) returns ≥1 candidate.
   - An urgent concern promotes `hospital_emergency` type to front.
   - An emergency concern exits before `find_services` (unchanged pipeline).
   - `final_check` passes on the built answer for a real hospital record.
3. Run full benchmark (`pytest tests/test_benchmark.py`) to confirm zero
   regression.

**Relevant context:**
- `tests/test_benchmark.py` — parametrised over `data/scenarios.json`.
- `AGENTS.md` — benchmark rules: under-triage = hard failure.

---

### Step 8 — Update docs and SPEC.md

**Intent:** Keep documentation accurate after the data source expansion.

**Expected outcomes:**
- `docs/SPEC.md` updated to mention `DATA_SOURCE`, `hospitals_us.json`, the
  state-picker dropdown, and the CSV build script.
- `docs/PLAN_REAL_DATA.md` (this file) has its sub-task statuses updated to
  done as each step completes.
- `.env.example` gains a `DATA_SOURCE=demo` entry with a comment.

**Todo list:**
1. Edit `docs/SPEC.md` — add paragraph under "Data files" describing the new
   sources and the `DATA_SOURCE` toggle.
2. Edit `.env.example` — add `DATA_SOURCE=demo  # or hospitals_us`.
3. No other doc changes required.

---

## Part 5 — Risks and Mitigations

### R1 — Closed hospitals
**Risk:** The CSV includes both OPEN and CLOSED hospitals. Recommending a
closed hospital is a hard safety failure.  
**Mitigation:** Step 1 (build script) filters `STATUS != "OPEN"` as the very
first operation. A test asserts that no record in `hospitals_us.json` has
`STATUS=CLOSED`. The field is not preserved in the output JSON.

---

### R2 — Missing values ("NOT AVAILABLE", -999)
**Risk:** ~20% of rows have no phone, some have no lat/lon. A missing phone
will fail `checks.py:_check_phones`. A missing lat/lon silently sets
`distance_km=None` (same as telemedicine — acceptable).  
**Mitigation:**
- Build script: normalise "NOT AVAILABLE" phone → empty string `""`.
- `checks.py` already skips the phone check when `phone_digits` is empty
  (`if phone_digits and …`). No code change needed.
- `advisor.py` treats `distance_km=None` as telemedicine (score 1.0) —
  acceptable for hospitals with unknown coordinates; they still appear in
  results but without a distance estimate.
- Build script summary reports count of null-phone and null-lat/lon rows so
  the team can assess data quality.

---

### R3 — County and city names repeat across states
**Risk:** "Montgomery" exists in Alabama, Texas, and 17 other states. Filtering
by city or county name alone would return results across state lines.  
**Mitigation:** The `area` filter key in `hospitals_us` mode is the two-letter
**STATE** code, not city or county. City/county are stored in the record for
display only. The haversine anchor is the state geographic centre, so distance
scores are approximate but consistent within a state.

---

### R4 — Old validation dates
**Risk:** Many `VAL_DATE` values are from 2013–2014. The freshness score in
`advisor.py` penalises records older than 60 days and zeroes out at 90 days.
With data from 2013, every record would score `freshness = 0` and every answer
would show the "call ahead" stale warning — which is actually **correct and
desirable** for old data.  
**Mitigation:**
- No code change needed; the existing stale-penalty logic handles this correctly.
- `checks.py:_check_last_verified` accepts "N days ago — call ahead" as the
  stale path — this will fire for all hospitals and pass the check.
- The build script stores the parsed ISO date in `last_verified`; null dates
  are stored as `null` and the check already skips null dates.
- A note is added to the UI caption: "Hospital data sourced from US public
  records; last verified dates may be several years old. Always call ahead."

---

### R5 — No emergency-department or hours field
**Risk:** The CSV has no `open_24h` or `hours` field.

**Decision (implemented):** `open_24h` was intentionally **not** implemented for
the hospitals_us path. The dataset contains no emergency-department field and no
opening-hours field, so there is no reliable basis for setting or displaying either
value. The planned mitigation (setting `open_24h = true` for `hospital_emergency`
type) was not carried out because it would have implied knowledge the data does not
support.

**What was done instead:**
- `advisor.py` scoring for the hospitals_us path does not use `open_24h`. Scoring
  uses type fit, trauma level, distance, and bed count.
- `checks.py` check 12 actively blocks any answer that claims
  "emergency department", "emergency room", "24-hour", or "opening hours".
- The UI and user guide direct users to call ahead to confirm a facility is open
  before travelling.

---

### R6 — Scale: 7,500+ OPEN records vs. 20 demo records
**Risk:** Loading all 7,500+ records into memory on every request is slower
than loading 20. Cache invalidation across `DATA_SOURCE` switches in tests.  
**Mitigation:**
- The existing module-level cache pattern in `finder.py` and `checks.py` is
  retained. The full list is loaded once per process, not per request.
- At ~200 bytes per record × 7,500 records ≈ 1.5 MB — well within normal
  process memory.
- For the scope of this MVP, no database or index is needed. A note in code
  marks the cache as the right place to add a DB call later.
- Tests that switch `DATA_SOURCE` must clear the caches; `service_loader.py`
  exposes a `_clear_cache()` helper for tests only.

---

### R7 — ID collision between demo SVC IDs and hospital IDs
**Risk:** If both data sources are ever loaded in the same process (e.g. in a
test that switches DATA_SOURCE mid-run), IDs could collide.  
**Mitigation:** Hospital IDs are prefixed `HSP-` (e.g. `HSP-0001336420`).
Demo IDs remain `SVC001`–`SVC020`. These namespaces cannot overlap.

---

## Open Questions for Review

1. **State vs. city granularity** — The plan uses the two-letter STATE code as
   the area key, with the state's geographic centre as the haversine anchor.
   This means a user in Houston and a user in El Paso both see the same "TX"
   pool, with distances measured from the state centroid (~Abilene). A more
   precise approach would let users pick STATE then CITY; that requires a
   two-level dropdown and a city-centres lookup file.  
   **Q: Is state-level granularity sufficient for the MVP, or do you want a
   state+city picker?**

2. **Hospital type mapping** — The plan maps all non-psychiatric/rehab types to
   `hospital_emergency`. If you want to distinguish children's hospitals,
   critical-access hospitals, or long-term care from general acute-care, the
   type table in Step 1 should be extended before the build script is written.  
   **Q: Is the current 3-type mapping (hospital_emergency / specialist / primary_care)
   sufficient, or should we add more granularity?**

3. **Phone format** — US telephone numbers in the CSV are formatted as
   `(334) 222-8466`. The existing `checks.py` phone check strips all
   non-digits and compares. This should work transparently, but the demo phones
   use a `[DEMO-XXXX]` format that contains no digits — so the check currently
   passes vacuously for demo data. For real data the check will be substantive.  
   **Q: No action needed on your side; confirming this is understood and
   acceptable.**

4. **`data/hospitals_us.json` file size** — At ~7,500 records × ~300 bytes each,
   the output JSON will be roughly 2–3 MB. This is fine for the repo but it is
   larger than current committed data files.  
   **Q: Should `data/hospitals_us.json` be committed to the repo, or should the
   build script be run as a setup step (and the JSON added to `.gitignore`)?**

5. **`last_verified` null handling in answer text** — When `last_verified` is
   null, the current `orchestrator.py` embeds `"Data last verified: None"` in
   the answer text, which would then fail the `_check_last_verified` check
   (the check skips null dates, but the answer text still shows "None").  
   **Q: Should null dates render as "Date not available — call ahead" in the
   answer text, or should we just skip the date line entirely for null dates?**

---

## Sub-Task Status Tracker

| Step | Sub-task | Status |
|------|----------|--------|
| 1 | CSV ingest script + cleaned JSON | [ ] pending |
| 2 | service_loader.py + state_centres.json + location_resolver.py | [ ] pending |
| 3 | Modify finder.py for dual-source | [ ] pending |
| 4 | Modify checks.py for dual-source | [ ] pending |
| 5 | Modify app.py area dropdown | [ ] pending |
| 6 | Update refresh_dates.py | [ ] pending |
| 7 | Benchmark scenarios + integration tests | [ ] pending |
| 8 | Update docs and SPEC.md | [ ] pending |
