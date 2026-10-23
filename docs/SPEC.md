# CareCompass MVP spec

CareCompass is a healthcare navigation assistant. It does NOT diagnose.
A user describes a worry in chat. The app checks safety, finds and compares
nearby services from a demo directory, and gives one clear next step.

## Stack
Python 3.11+, Streamlit chat UI, pytest, JSON data files.
LLM is optional (watsonx.ai through environment variables). Every LLM step
has an offline fallback (keyword extraction and templated wording).

## Core flow (8 steps)
1. Describe: user types the situation and picks an area from a list.
2. Understand: Intake extracts a structured case (who, concern, duration,
   severity cues, area). Ask at most 2 follow-up questions.
3. Safety check: red-flag rules in code, then a Safety review. Highest tier
   wins: emergency, urgent, routine.
   - Emergency: show the verbatim message from data/messages.json plus the
     local emergency number from config. No search. Offer a handoff summary.
4. Find: map the case to service types, filter data/services.json by type,
   compute straight-line distance (haversine) from the chosen area.
5. Compare: rank top 3 by fit, distance, open status, data freshness.
   Urgent cases weight distance more. Reasons come from record fields only.
6. Final check: every service ID and fact exists in services.json; no
   diagnosis or treatment wording; urgent advice present when tier is urgent.
   If it fails, revise at most 2 times, then show a "needs human review" notice.
7. Next step: one recommendation plus a fallback, with reasons, service ID,
   distance and last-verified date.
8. Handoff: only after the user clicks Yes, create a short summary text.

## Data files (all synthetic, labelled "demo data")
- data/services.json: ~20 services in 5 demo areas
- data/red_flags.json: ~10 demo rule categories with phrases and tier
- data/messages.json: verbatim emergency and urgent messages
- data/scenarios.json: ~12 test scenarios with expected tier and service type

## Definition of done
- All emergency scenarios give the emergency message with no LLM and no network.
- Every recommendation shows service ID, distance, reason, last-verified date.
- Urgent and routine rank services differently.
- Automated check finds no diagnosis wording.
- Handoff summary only after explicit consent.
- Every step is written to logs/audit.jsonl.
- Demo scenarios run end to end in under 30 seconds.
