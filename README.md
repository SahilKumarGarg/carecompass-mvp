# CareCompass MVP

CareCompass is a healthcare navigation assistant built for the IBM SkillsBuild programme.
A user describes a health concern in chat, picks their area, and the app works out
how urgent the situation is, finds matching local services from a demo directory, and
gives one clear next step — including a phone number and the date the data was last
verified.

**It does not diagnose. It does not suggest medicines or treatments. It directs people
to the right service.**

> ⚠ All data in this repository is entirely synthetic demo data. Nothing here is
> medical advice. Never use this application for real clinical decisions.

---

## Install and run

**Requirements:** Python 3.11 or newer.

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy the environment file and optionally fill in watsonx.ai credentials
#    (leaving them blank runs the fully offline fallback)
cp .env.example .env

# 4. Start the app
streamlit run app.py
```

The app opens in your browser at `http://localhost:8501`.

### Optional: watsonx.ai integration

Open `.env` and set `WATSONX_API_KEY`, `WATSONX_URL`, and `WATSONX_PROJECT_ID`.
When these are present the intake step uses the Granite model to extract structured
fields from the user's message, and the advisor uses it to reword recommendation
reasons. **Every step has an offline fallback — watsonx.ai is never required.**

Set `EMERGENCY_NUMBER` to the local emergency phone number shown in the emergency
message (defaults to `999` if unset).

---

## Run the tests

```bash
# All tests
pytest

# Single file
pytest tests/test_safety.py

# Single test function
pytest tests/test_safety.py::test_emergency_scenarios_return_emergency_tier

# Benchmark (prints a summary table, run with -s to see output)
pytest tests/test_benchmark.py -v -s
```

The test suite runs fully offline with no credentials needed (209 tests, ~0.7 s).

---

## The 8-step flow

Each user turn passes through a fixed pipeline. Every step and decision is written
to `logs/audit.jsonl`.

| Step | Name | What happens |
|------|------|--------------|
| 1 | **Describe** | User types a concern and picks an area from the dropdown. |
| 2 | **Understand** | Intake extracts who the concern is about, the concern itself, duration, and severity cues. Up to 2 follow-up questions are asked when fields are missing. |
| 3 | **Safety check** | Red-flag rules (plain Python, no LLM) scan the text for emergency and urgent phrases. Highest tier wins. **Emergency: show the verbatim emergency message, skip steps 4–7.** |
| 4 | **Find** | The case maps to preferred service types via a keyword table. Services in the chosen area are filtered from `data/services.json`. Straight-line distance is estimated from the area centre. |
| 5 | **Compare** | Candidates are scored on type fit, distance, open status, and data freshness. Urgent cases weight distance and open status more heavily. Top 3 + a fallback are selected. |
| 6 | **Final check** | Every service ID, name, and phone in the answer is verified against `data/services.json`. Banned diagnosis and treatment phrases are checked. The last-verified date must appear. Urgent answers must contain urgent-action language. On failure the answer is revised and re-checked — at most 2 retries. |
| 7 | **Present** | The recommendation is shown: service card, distance estimate (labelled as estimate), opening hours, phone, last-verified date, and reason. |
| 8 | **Handoff** | Only after the user clicks **Yes**, a short plain-text summary is created to share with a healthcare professional. Clicking **No** creates nothing. |

---

## All data is synthetic

| File | Contents |
|------|----------|
| `data/services.json` | 20 synthetic services across 5 demo areas. All names, phone numbers, and addresses are invented. |
| `data/red_flags.json` | 10 demo red-flag categories with trigger phrases and urgency tiers. |
| `data/messages.json` | Verbatim emergency, urgent, and review messages shown to the user. |
| `data/scenarios.json` | 12 test scenarios (4 emergency, 4 urgent, 4 routine) with expected outcomes. |
| `data/care_levels.json` | Keyword → service-type mapping used by the finder. |
| `data/area_centres.json` | Approximate centre coordinates per area for distance estimation. |
| `data/banned_phrases.json` | Phrases the app must never produce (diagnosis and treatment language). |

Every data file carries `"demo_data": true`. No real patient data is used anywhere.

---

## Known limits of this MVP

- **Offline tier detection is keyword-only.** The safety check catches emergency and
  urgent phrases exactly as written. Indirect or ambiguous descriptions of urgent
  conditions (e.g. "my asthma inhaler isn't working") are not escalated to urgent
  without the watsonx.ai intake step.

- **Open-hours parsing is not implemented.** The ranking scores a service as 0.5
  ("not 24h") regardless of whether it is open right now. A Mon–Fri GP practice
  scores the same on Saturday as on Tuesday.

- **Distance is from the area centre, not the user's location.** All services in
  the same area are within a few hundred metres of each other in the demo data,
  so distance contributes little to ranking between same-area candidates.

- **Follow-up questions are generated but not yet displayed in the UI.** The
  orchestrator detects missing intake fields and produces a follow-up question,
  but `app.py` does not currently surface it before running the pipeline.

- **The LLM reword path (`guardrails=False`) is not independently validated.**
  The final check catches banned phrases in the full answer text, but only after
  the reworded reason is already stored. The banned-phrase list also has gaps
  for interpretive language ("this suggests", "consistent with").

- **The final-check revision is cosmetic.** On a failed check the orchestrator
  appends a disclaimer and retries. This fixes the urgent-advice check but not
  structural problems like a missing phone number or date.

- **5 demo areas only.** Greenfield, Riverside, Northgate, Eastfield, Westbury —
  all synthetic. An unknown area returns no candidates.

---

## Project layout

```
carecompass-mvp/
├── app.py                  Streamlit UI
├── requirements.txt
├── .env.example
├── data/                   Synthetic demo data (JSON)
├── src/
│   ├── safety.py           Red-flag phrase matching (no LLM)
│   ├── intake.py           Case extraction (offline + optional watsonx.ai)
│   ├── finder.py           Service filtering and distance estimation
│   ├── advisor.py          Ranking and reason building
│   ├── checks.py           Final-check validation
│   ├── orchestrator.py     8-step state machine
│   └── audit.py            Audit log writer
├── tests/                  pytest test suite (209 tests)
├── docs/
│   └── SPEC.md             Full technical specification
└── logs/                   audit.jsonl written here at runtime (not committed)
```
