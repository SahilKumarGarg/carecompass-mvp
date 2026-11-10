"""Final-check module: validate a recommendation answer before it is shown to the user.

Checks performed (in order):
1. Every service ID cited in the answer exists in services.json  (demo path only).
2. Every service name cited in the answer exists in services.json  (demo path only).
3. Every phone number cited in the answer exists in services.json  (demo path only).
4. The answer contains no banned diagnosis or treatment phrases.
5. The last-verified date for each cited service appears in the answer text,
   OR a "days ago — call ahead" stale warning is present (for stale services).  [demo only]
6. When tier is "urgent", the answer contains an urgent-action prompt.
7. [hospitals_us] Every hospital ID cited exists in hospitals_clean.csv.
8. [hospitals_us] Every hospital name cited appears in the answer text.
9. [hospitals_us] Every hospital phone cited appears in the answer text.
10. [hospitals_us] Every hospital validation year cited appears in the answer text.
11. [hospitals_us] Answer includes "call ahead to confirm" line.
12. [hospitals_us] Answer never claims emergency department, 24-hour service or opening hours.
13. [hospitals_us] No closed hospital type (REHABILITATION, MILITARY, etc.) in cited hospitals.

Returns CheckResult(passed=True) or CheckResult(passed=False, problems=[...]).
"""

from __future__ import annotations

import csv
import dataclasses
import json
import re
import unicodedata
from pathlib import Path

_DATA = Path(__file__).parent.parent / "data"
_SERVICES_PATH = _DATA / "services.json"
_BANNED_PHRASES_PATH = _DATA / "banned_phrases.json"
_HOSPITALS_PATH = _DATA / "hospitals_clean.csv"

# Phrases that indicate urgent escalation advice is present in the text.
_URGENT_ADVICE_MARKERS = [
    "today",
    "same day",
    "same-day",
    "urgently",
    "as soon as possible",
    "without delay",
    "do not wait",
    "don't wait",
    "within",
    "immediately",
    "right away",
    "straight away",
    "get help",
    "seek help",
    "contact",
    "call",
    "attend",
    "go to",
]

# Phrases that must never appear in answers for the hospitals_us path.
_FORBIDDEN_HOSPITAL_CLAIMS = (
    "emergency department",
    "emergency room",
    "24-hour",
    "24 hour",
    "open 24",
    "opening hours",
    "open hours",
)

# Hospital TYPE values that are excluded from results (closed/inappropriate).
_CLOSED_HOSPITAL_TYPES = frozenset({
    "MILITARY", "LONG TERM CARE", "REHABILITATION", "CHRONIC DISEASE", "SPECIAL",
})


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class CheckResult:
    """Result of a final_check call."""

    passed: bool
    problems: list[str]     # empty when passed is True


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

_services_index: dict[str, dict] | None = None
_banned_cache: list[str] | None = None
_hospitals_index: dict[str, dict] | None = None


def _load_services_index() -> dict[str, dict]:
    """Return a dict keyed by service ID."""
    global _services_index
    if _services_index is None:
        with _SERVICES_PATH.open(encoding="utf-8") as fh:
            raw = json.load(fh)["services"]
        _services_index = {s["id"]: s for s in raw}
    return _services_index


def _load_banned_phrases() -> list[str]:
    """Return a flat list of all banned phrases."""
    global _banned_cache
    if _banned_cache is None:
        with _BANNED_PHRASES_PATH.open(encoding="utf-8") as fh:
            categories = json.load(fh)["banned_phrases"]
        _banned_cache = [p for cat in categories for p in cat["phrases"]]
    return _banned_cache


def _load_hospitals_index() -> dict[str, dict]:
    """Return a dict keyed by hospital ID from hospitals_clean.csv."""
    global _hospitals_index
    if _hospitals_index is None:
        with _HOSPITALS_PATH.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            _hospitals_index = {row["ID"]: row for row in reader}
    return _hospitals_index


# ---------------------------------------------------------------------------
# Normalisation (shared with safety.py approach)
# ---------------------------------------------------------------------------

def _normalise(text: str) -> str:
    """Lowercase, strip accents, collapse punctuation/whitespace."""
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Individual check functions — demo path
# ---------------------------------------------------------------------------

def _check_service_ids(answer_text: str, cited_ids: list[str],
                       index: dict[str, dict]) -> list[str]:
    """Return problems for any cited service ID not found in services.json."""
    problems: list[str] = []
    for sid in cited_ids:
        if sid not in index:
            problems.append(f"Service ID '{sid}' not found in services.json.")
    return problems


def _check_service_names(answer_text: str, cited_ids: list[str],
                         index: dict[str, dict]) -> list[str]:
    """Return problems for any cited ID whose name does not appear in the answer text."""
    problems: list[str] = []
    norm_text = _normalise(answer_text)
    for sid in cited_ids:
        if sid not in index:
            continue  # already reported above
        name = index[sid]["name"]
        if _normalise(name) not in norm_text:
            problems.append(
                f"Service name '{name}' (ID {sid}) is cited but does not appear in the answer."
            )
    return problems


def _check_phones(answer_text: str, cited_ids: list[str],
                  index: dict[str, dict]) -> list[str]:
    """Return problems for any cited service whose phone number is absent from the answer."""
    problems: list[str] = []
    # Strip all non-digits from answer for phone comparison.
    digits_in_answer = re.sub(r"\D", "", answer_text)
    for sid in cited_ids:
        if sid not in index:
            continue
        phone_raw = index[sid]["phone"]
        phone_digits = re.sub(r"\D", "", phone_raw)
        if phone_digits and phone_digits not in digits_in_answer:
            problems.append(
                f"Phone number '{phone_raw}' for service {sid} is missing from the answer."
            )
    return problems


def _check_banned_phrases(answer_text: str) -> list[str]:
    """Return problems for any banned phrase found in the answer."""
    norm_text = _normalise(answer_text)
    banned = _load_banned_phrases()
    problems: list[str] = []
    for phrase in banned:
        if _normalise(phrase) in norm_text:
            problems.append(f"Banned phrase found in answer: '{phrase}'.")
    return problems


def _check_last_verified(answer_text: str, cited_ids: list[str],
                         index: dict[str, dict]) -> list[str]:
    """Return problems for any cited service whose verification info is absent from the answer.

    Passes when the answer contains either:
    - the raw ISO date string (fresh services), or
    - a "days ago" stale warning that includes "call ahead" (stale services).
    """
    import datetime as _dt
    problems: list[str] = []
    for sid in cited_ids:
        if sid not in index:
            continue
        date_str = index[sid].get("last_verified", "")
        if not date_str:
            continue
        # Fresh path: date appears verbatim in the answer.
        if date_str in answer_text:
            continue
        # Stale path: if the service is stale, the advisor replaces the date with
        # "Last verified N days ago — call ahead …".  Accept this as valid.
        try:
            verified = _dt.date.fromisoformat(date_str)
            age = ((_dt.date.today() - verified).days)
        except ValueError:
            age = None
        if age is not None and str(age) in answer_text and "call ahead" in answer_text.lower():
            continue
        problems.append(
            f"last_verified date '{date_str}' for service {sid} is missing from the answer."
        )
    return problems


def _check_urgent_advice(answer_text: str, tier: str) -> list[str]:
    """When tier is 'urgent', verify at least one urgent-action marker is present."""
    if tier != "urgent":
        return []
    norm_text = _normalise(answer_text)
    for marker in _URGENT_ADVICE_MARKERS:
        if _normalise(marker) in norm_text:
            return []
    return [
        "Tier is 'urgent' but the answer contains no urgent-action language "
        "(e.g. 'today', 'as soon as possible', 'call', 'do not wait')."
    ]


# ---------------------------------------------------------------------------
# Individual check functions — hospitals_us path
# ---------------------------------------------------------------------------

def _check_hospital_ids(cited_ids: list[str], index: dict[str, dict]) -> list[str]:
    """Return problems for any cited hospital ID not found in hospitals_clean.csv."""
    problems: list[str] = []
    for hid in cited_ids:
        if hid not in index:
            problems.append(f"Hospital ID '{hid}' not found in hospitals_clean.csv.")
    return problems


def _check_hospital_names(answer_text: str, cited_ids: list[str],
                           index: dict[str, dict]) -> list[str]:
    """Return problems for any cited hospital whose name does not appear in the answer."""
    problems: list[str] = []
    norm_text = _normalise(answer_text)
    for hid in cited_ids:
        if hid not in index:
            continue
        name = index[hid].get("NAME", "") or index[hid].get("display_name", "")
        # Title-case version is used in the answer.
        name_title = name.title()
        if _normalise(name) not in norm_text and _normalise(name_title) not in norm_text:
            problems.append(
                f"Hospital name '{name_title}' (ID {hid}) is cited but does not appear in the answer."
            )
    return problems


def _check_hospital_phones(answer_text: str, cited_ids: list[str],
                            index: dict[str, dict]) -> list[str]:
    """Return problems for any cited hospital whose phone number is absent from the answer."""
    problems: list[str] = []
    digits_in_answer = re.sub(r"\D", "", answer_text)
    for hid in cited_ids:
        if hid not in index:
            continue
        phone_raw = index[hid].get("TELEPHONE", "")
        phone_digits = re.sub(r"\D", "", phone_raw)
        if phone_digits and phone_digits not in digits_in_answer:
            problems.append(
                f"Phone '{phone_raw}' for hospital {hid} is missing from the answer."
            )
    return problems


def _check_hospital_val_year(answer_text: str, cited_ids: list[str],
                              index: dict[str, dict]) -> list[str]:
    """Return problems for any cited hospital whose validation year is absent from the answer."""
    problems: list[str] = []
    for hid in cited_ids:
        if hid not in index:
            continue
        val_year = index[hid].get("VAL_DATE", "")
        if val_year and val_year not in answer_text:
            problems.append(
                f"Validation year '{val_year}' for hospital {hid} is missing from the answer."
            )
    return problems


def _check_call_ahead_present(answer_text: str, cited_ids: list[str]) -> list[str]:
    """Return a problem when any hospital is cited but 'call ahead' is absent."""
    if not cited_ids:
        return []
    if "call ahead" not in answer_text.lower():
        return ["Answer cites hospitals but missing required 'call ahead to confirm' line."]
    return []


def _check_forbidden_hospital_claims(answer_text: str) -> list[str]:
    """Return problems for any phrase that claims ED, 24-hour or opening-hours info."""
    norm_text = _normalise(answer_text)
    problems: list[str] = []
    for phrase in _FORBIDDEN_HOSPITAL_CLAIMS:
        if _normalise(phrase) in norm_text:
            problems.append(f"Answer must not claim '{phrase}' (not verified from CSV data).")
    return problems


def _check_no_closed_hospitals(cited_ids: list[str], index: dict[str, dict]) -> list[str]:
    """Return problems for any cited hospital with an excluded (closed/inappropriate) TYPE."""
    problems: list[str] = []
    for hid in cited_ids:
        if hid not in index:
            continue
        htype = index[hid].get("TYPE", "").upper()
        if htype in _CLOSED_HOSPITAL_TYPES:
            problems.append(
                f"Hospital '{hid}' has excluded TYPE '{htype}' and must not appear in answers."
            )
    return problems


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def final_check(
    answer_text: str,
    cited_service_ids: list[str],
    tier: str = "routine",
    hospital_ids: list[str] | None = None,
) -> CheckResult:
    """Validate an answer before it is shown to the user.

    When hospital_ids is provided (hospitals_us path), the function validates
    against hospitals_clean.csv: checks ID existence, name/phone/val_year in
    answer, 'call ahead' line presence, no forbidden claims (ED/24h/hours), and
    no closed hospital types.

    When hospital_ids is None (demo path), validates against services.json as
    before: service ID/name/phone existence, last_verified date, urgent advice.

    Banned-phrase and urgent-advice checks always run regardless of path.

    Args:
        answer_text: The full recommendation text that will be shown to the user.
        cited_service_ids: List of service IDs referenced in the answer (demo path).
        tier: Safety tier — "emergency" | "urgent" | "routine".
        hospital_ids: List of hospital IDs from hospitals_clean.csv (hospitals_us path).
                      When supplied, the hospital grounding checks run instead of the
                      demo service checks.

    Returns:
        CheckResult(passed=True, problems=[]) on success, or
        CheckResult(passed=False, problems=[...]) listing each problem found.
    """
    problems: list[str] = []

    if hospital_ids is not None:
        # ── hospitals_us path ──────────────────────────────────────────────
        hosp_index = _load_hospitals_index()
        problems += _check_hospital_ids(hospital_ids, hosp_index)
        problems += _check_hospital_names(answer_text, hospital_ids, hosp_index)
        problems += _check_hospital_phones(answer_text, hospital_ids, hosp_index)
        problems += _check_hospital_val_year(answer_text, hospital_ids, hosp_index)
        problems += _check_call_ahead_present(answer_text, hospital_ids)
        problems += _check_forbidden_hospital_claims(answer_text)
        problems += _check_no_closed_hospitals(hospital_ids, hosp_index)
    else:
        # ── demo path ─────────────────────────────────────────────────────
        index = _load_services_index()
        problems += _check_service_ids(answer_text, cited_service_ids, index)
        problems += _check_service_names(answer_text, cited_service_ids, index)
        problems += _check_phones(answer_text, cited_service_ids, index)
        problems += _check_last_verified(answer_text, cited_service_ids, index)

    # These checks always run for both paths.
    problems += _check_banned_phrases(answer_text)
    problems += _check_urgent_advice(answer_text, tier)

    return CheckResult(passed=len(problems) == 0, problems=problems)
