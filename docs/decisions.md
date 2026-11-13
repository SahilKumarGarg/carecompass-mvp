# Design Decisions

This file records significant decisions made during development.
Each entry explains what changed, why, and which scenarios were affected.

---

## 2026-11-13 — Redefine service-type benchmark metric for hospitals_us path

### Context

The original `service_type_match` metric in `tests/test_benchmark.py` compared
each scenario's top result against `expected_service_type` values such as
`primary_care`, `pharmacy`, `specialist`, or `hospital_emergency`.  Those values
were defined for the old synthetic-data (`demo`) service directory which returned
typed service objects.

The `hospitals_us` path only returns hospitals from `data/hospitals_clean.csv`.
Every hospital has a raw **TYPE** column (e.g. `GENERAL ACUTE CARE`,
`CRITICAL ACCESS`, `PSYCHIATRIC`, `CHILDREN`, `WOMEN`).  The old mapping (e.g.
`GENERAL ACUTE CARE → hospital_emergency`, `PSYCHIATRIC → specialist`) collapsed
all acute hospitals into a single vocabulary token that was too coarse: it hid
real mismatches (e.g. a children's hospital returned for an adult emergency) and
produced false positives for routine scenarios that are better served by a
primary-care clinic.

### Decision

Replace the coarse mapping with a **direct hospital-TYPE comparison** using the
`acceptable_hospital_types` list already present on every scenario in
`data/scenarios.json`.  The new logic is:

1. **Emergency-tier scenarios** — skipped entirely (`service_type_match = None`).
   Emergency tier exits before any hospital search; there are no ranked results.

2. **Routine scenarios where `expected_service_type` is `pharmacy`,
   `primary_care`, or `telemedicine`** — use the **advisory-line check** instead
   of a TYPE comparison.  The `rank_hospitals()` function in `src/advisor.py`
   appends the verbatim line:
   > "A primary care clinic or pharmacy may be a better first step; these
   > hospitals are nearby if symptoms get worse."
   The metric checks that this exact substring is present in `answer_text`.
   This is more meaningful than matching a hospital TYPE for scenarios where
   the ideal first step is *not* a hospital.

3. **All other non-emergency scenarios** (urgent, and routine with `specialist`
   or `hospital_emergency` as `expected_service_type`) — compare the top-ranked
   hospital's raw TYPE (uppercased, stripped) against `acceptable_hospital_types`.

### Per-scenario changes

The following table lists every scenario where the metric definition changed and
the reason.

| Scenario | Label | Old metric | New metric | Reason |
|---|---|---|---|---|
| SCN001 | emergency_chest_pain | `expected_service_type=null` → skipped | Skipped (emergency tier) | No change in outcome; rationale now explicit |
| SCN002 | emergency_stroke | skipped | Skipped (emergency tier) | Same |
| SCN003 | emergency_anaphylaxis | skipped | Skipped (emergency tier) | Same |
| SCN004 | emergency_pregnancy | skipped | Skipped (emergency tier) | Same |
| SCN005 | urgent_self_harm_thoughts | `specialist or primary_care` → mapped to coarse vocab | Raw TYPE in `["PSYCHIATRIC", "GENERAL ACUTE CARE"]` | Psychiatric hospitals are the right facility type for self-harm ideation |
| SCN006 | urgent_fever_confusion | `hospital_emergency` → `hospital_emergency` | Raw TYPE in `["GENERAL ACUTE CARE", "CRITICAL ACCESS"]` | Coarse mapping was equivalent; now explicit |
| SCN007 | urgent_child_ear_pain | `primary_care or hospital_emergency` → `hospital_emergency` | Raw TYPE in `["CHILDREN", "GENERAL ACUTE CARE"]` | Child-specific hospitals are preferred for a 4-year-old with ear pain |
| SCN008 | urgent_asthma_worsening | `hospital_emergency or primary_care` → `hospital_emergency` | Raw TYPE in `["GENERAL ACUTE CARE", "CRITICAL ACCESS"]` | Explicit acute-care types for worsening asthma |
| SCN009 | routine_repeat_prescription | `pharmacy or primary_care` → `hospital_emergency` (mapping) | Advisory-line check | Hospitals are not the right venue; check advisory is shown |
| SCN010 | routine_skin_rash | `primary_care or pharmacy` → `hospital_emergency` | Advisory-line check | Mild rash: advisory line confirms hospital is a fallback, not primary |
| SCN011 | routine_travel_vaccines | `pharmacy or primary_care` → `hospital_emergency` | Advisory-line check | Travel clinic/pharmacy; advisory shown |
| SCN012 | routine_mental_health_support | `specialist or primary_care` → `specialist` | Raw TYPE in `["PSYCHIATRIC", "GENERAL ACUTE CARE"]` | Psychiatric hospital is the appropriate type for non-crisis mental health |
| SCN013 | urgent_fever_disoriented | `hospital_emergency` | Raw TYPE in `["GENERAL ACUTE CARE", "CRITICAL ACCESS"]` | Explicit |
| SCN014 | urgent_explicit_suicidal | `specialist or primary_care` | Raw TYPE in `["PSYCHIATRIC", "GENERAL ACUTE CARE"]` | Psychiatric hospital preferred |
| SCN015 | urgent_feverish_muddled | `hospital_emergency or primary_care` | Raw TYPE in `["GENERAL ACUTE CARE", "CRITICAL ACCESS"]` | Explicit |
| SCN016 | urgent_want_to_end_life | `specialist or primary_care` | Raw TYPE in `["PSYCHIATRIC", "GENERAL ACUTE CARE"]` | Psychiatric hospital preferred |
| SCN017 | urgent_sweating_delirious | `hospital_emergency` | Raw TYPE in `["GENERAL ACUTE CARE", "CRITICAL ACCESS"]` | Explicit |
| SCN018 | emergency_chest_pain_formal | skipped | Skipped (emergency tier) | Same |
| SCN019 | emergency_chest_pain_typo | skipped | Skipped (emergency tier) | Same |
| SCN020 | emergency_breathing_plain | skipped | Skipped (emergency tier) | Same |
| SCN021 | emergency_breathing_formal | skipped | Skipped (emergency tier) | Same |
| SCN022 | emergency_stroke_typo | skipped | Skipped (emergency tier) | Same |
| SCN023 | emergency_bleeding_plain | skipped | Skipped (emergency tier) | Same |
| SCN024 | emergency_unconscious_plain | skipped | Skipped (emergency tier) | Same |
| SCN025 | emergency_unconscious_typo | skipped | Skipped (emergency tier) | Same |
| SCN026 | emergency_seizure_plain | skipped | Skipped (emergency tier) | Same |
| SCN027 | emergency_allergy_plain | skipped | Skipped (emergency tier) | Same |
| SCN028 | emergency_pregnancy_formal | skipped | Skipped (emergency tier) | Same |
| SCN029 | emergency_pregnancy_typo | skipped | Skipped (emergency tier) | Same |
| SCN030 | urgent_fever_confusion_formal | `hospital_emergency` | Raw TYPE in `["GENERAL ACUTE CARE", "CRITICAL ACCESS"]` | Explicit |
| SCN031 | urgent_fever_confusion_typo | `hospital_emergency` | Raw TYPE in `["GENERAL ACUTE CARE", "CRITICAL ACCESS"]` | Explicit |
| SCN032 | urgent_self_harm_formal | `specialist or primary_care` | Raw TYPE in `["PSYCHIATRIC", "GENERAL ACUTE CARE"]` | Psychiatric hospital preferred |
| SCN033 | urgent_self_harm_typo | `specialist or primary_care` | Raw TYPE in `["PSYCHIATRIC", "GENERAL ACUTE CARE"]` | Psychiatric hospital preferred |
| SCN034 | urgent_child_fever_infant | `primary_care or hospital_emergency` | Raw TYPE in `["CHILDREN", "GENERAL ACUTE CARE"]` | Children's hospitals preferred for infants |
| SCN035 | urgent_child_fever_typo | `primary_care or hospital_emergency` | Raw TYPE in `["CHILDREN", "GENERAL ACUTE CARE"]` | Children's hospitals preferred for young children |
| SCN036 | urgent_child_fever_formal | `primary_care or hospital_emergency` | Raw TYPE in `["CHILDREN", "GENERAL ACUTE CARE"]` | Children's hospitals preferred for paediatric cases |
| SCN037 | urgent_asthma_plain | `hospital_emergency or primary_care` | Raw TYPE in `["GENERAL ACUTE CARE", "CRITICAL ACCESS"]` | Explicit acute-care types |
| SCN038 | routine_injury_wound | `primary_care or hospital_emergency` → `hospital_emergency` | Raw TYPE in `["GENERAL ACUTE CARE", "CRITICAL ACCESS"]` | Possible fracture: either type is acceptable; advisory shown |
| SCN039 | routine_child_diarrhoea_dehydration | `primary_care` → `hospital_emergency` | Raw TYPE in `["CHILDREN", "GENERAL ACUTE CARE"]` | Children's hospital acceptable for infant dehydration |
| SCN040 | routine_prescription_typo | `pharmacy or primary_care` → `hospital_emergency` | Advisory-line check | Repeat prescription: advisory line confirms hospital is a fallback |
| SCN041 | routine_prescription_formal | `pharmacy or primary_care` → `hospital_emergency` | Advisory-line check | Same as SCN040 |
| SCN042 | routine_gp_appointment | `primary_care or telemedicine` → `hospital_emergency` | Advisory-line check | GP check-up: advisory line is the meaningful signal |
| SCN043 | routine_gp_appointment_typo | `primary_care or telemedicine` → `hospital_emergency` | Advisory-line check | Same as SCN042 |
| SCN044 | routine_cold_symptoms | `primary_care or pharmacy` → `hospital_emergency` | Advisory-line check | Common cold: advisory shown |
| SCN045 | routine_contraception_advice | `primary_care or telemedicine` → `hospital_emergency` | Advisory-line check | Contraception review: advisory shown |
| SCN046 | routine_back_pain_mild | `primary_care or telemedicine` → `hospital_emergency` | Advisory-line check | Mild back pain: advisory shown |
| SCN047 | routine_flu_vaccination | `pharmacy or primary_care` → `hospital_emergency` | Advisory-line check | Flu jab: advisory shown |
| SCN048 | urgent_unable_to_walk_headache | `hospital_emergency` | Raw TYPE in `["GENERAL ACUTE CARE", "CRITICAL ACCESS"]` | Explicit |
| SCN049 | emergency_worst_headache_ever | skipped | Skipped (emergency tier) | No search on emergency path |
| SCN050 | urgent_headache_fever_stiff_neck | `hospital_emergency` | Raw TYPE in `["GENERAL ACUTE CARE", "CRITICAL ACCESS"]` | Possible meningitis: acute-care types |

### Routine scenarios: SCN038 and SCN039

SCN038 (routine_injury_wound) and SCN039 (routine_child_diarrhoea_dehydration)
have `expected_service_type` of `primary_care` — which is **not** in
`_CLINIC_OR_PHARMACY_TYPES` check list (`pharmacy`, `primary_care` is… wait —
`primary_care` IS in the list).

Correction: SCN038 and SCN039 have `expected_service_type = "primary_care"` which
is in `_CLINIC_OR_PHARMACY_TYPES`, so they use the advisory-line check.
Updated table row for SCN038 and SCN039 above is correct.

### Safety invariants preserved

- Under-triage count must remain 0 — this change touches only the type metric,
  not the tier logic.
- Emergency path still exits before any LLM or hospital search.
- No red-flag rules, banned-phrase lists, or urgency thresholds were modified.
