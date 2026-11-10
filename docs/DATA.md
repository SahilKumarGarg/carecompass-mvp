# Data Dictionary — hospitals_clean.csv

## Source

**Dataset**: US Hospitals  
**Origin**: Kaggle — [https://www.kaggle.com/datasets/andrewmvd/us-hospitals](https://www.kaggle.com/datasets/andrewmvd/us-hospitals)  
**Licence**: Please consult the Kaggle dataset page for the applicable licence terms before use.  
**Raw file**: `data/raw/Hospitals.csv` (not committed — download separately and place at this path before running the preparation script)

## How to regenerate

```
python scripts/prepare_hospitals.py
```

Reads `data/raw/Hospitals.csv` → writes `data/hospitals_clean.csv`.

---

## Columns

| Column | Type | Notes |
|---|---|---|
| `ID` | text | Unique facility identifier from the source dataset |
| `NAME` | text | Hospital name, **upper-cased** for matching/search |
| `display_name` | text | Hospital name in **title case** for on-screen display |
| `ADDRESS` | text | Street address; mixed case as supplied |
| `CITY` | text | City name, **upper-cased** for matching |
| `STATE` | text | Two-letter state abbreviation, **upper-cased** |
| `ZIP` | text | 5-character ZIP code, **zero-padded** to preserve leading zeros (e.g. `02101`) |
| `TELEPHONE` | text | Main phone number as supplied; empty if not available |
| `TYPE` | text | Facility type (e.g. `GENERAL ACUTE CARE`, `CRITICAL ACCESS`) |
| `COUNTY` | text | County name, **upper-cased** for matching |
| `COUNTYFIPS` | text | 5-digit FIPS county code |
| `LATITUDE` | float | WGS-84 decimal degrees |
| `LONGITUDE` | float | WGS-84 decimal degrees |
| `BEDS` | integer | Licensed/staffed bed count; empty if not reported. **See note below.** |
| `TRAUMA` | text | Trauma designation level (e.g. `LEVEL I`); empty if none reported |
| `HELIPAD` | text | `Y` / `N` / empty |
| `WEBSITE` | text | Facility website URL; empty if not available |
| `OWNER` | text | Ownership category (e.g. `NON-PROFIT`, `PROPRIETARY`, `GOVERNMENT - FEDERAL`) |
| `VAL_DATE` | text | **Year only** of the most recent validation (original field contained a full timestamp) |
| `SOURCEDATE` | text | **Year only** of the source record date |

---

## Cleaning rules applied by `prepare_hospitals.py`

1. **Status filter** — only rows where `STATUS == OPEN` are kept.
2. **Missing-value normalisation** — `"NOT AVAILABLE"`, `"-999"`, and blank strings are all converted to an empty string.
3. **ZIP padding** — the raw ZIP field is zero-padded to 5 characters.
4. **BEDS** — the raw column is named `POPULATION` in older exports; `prepare_hospitals.py` reads the `BEDS` column directly and converts negative values (the source sentinel for "unknown") to empty.
5. **Coordinate validity** — rows without a parseable `LATITUDE` **and** `LONGITUDE` are dropped.
6. **Duplicate IDs** — if the same `ID` appears more than once, only the first occurrence is kept.
7. **Case normalisation** — `NAME`, `CITY`, `STATE`, and `COUNTY` are stored upper-cased in their primary column so downstream matching is case-insensitive. The human-readable `display_name` column stores the original name in title case.
8. **Date truncation** — `VAL_DATE` and `SOURCEDATE` are reduced to the 4-digit year.

---

## Known limitations

| Limitation | Detail |
|---|---|
| No emergency-department field | The source dataset does not indicate whether a facility has an emergency department; all `TYPE` filtering must rely on facility-type strings |
| No opening-hours field | There is no structured hours-of-operation data in the source; the application cannot determine whether a facility is currently open |
| Validation years | `VAL_DATE` values in the cleaned file range from **2013 to 2022**; some records may be outdated |
| BEDS caveat | The `BEDS` column was labelled `POPULATION` in the original Esri shapefile export; it represents licensed bed counts from that point in time and may not reflect current capacity |
| US-only | Coverage is limited to US facilities; territories and overseas bases may be incomplete |
| Demo data label | This file is generated from a public dataset for **demonstration purposes only**. Do not rely on it for real clinical or emergency routing decisions |
