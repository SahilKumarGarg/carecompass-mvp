"""Orchestrator: drives the 8-step CareCompass pipeline as a state machine.

Steps: describe → understand → safety → find → compare →
       final_check (≤2 retries) → present → handoff

Emergency tier exits immediately after safety with no LLM and no search.
A failed final_check revises at most 2 times; on persistent failure returns
a needs-human-review notice.

All steps and decisions are written to logs/audit.jsonl via src.audit.

DATA_SOURCE env var selects the data backend:
  hospitals_us (default) — uses data/hospitals_clean.csv via locations.resolve_location.
  demo                   — uses data/services.json (original behaviour).

The area parameter to run_session is interpreted differently per source:
  hospitals_us — expects a dict-like string encoded as JSON, or a plain string used
                 as a free-form location hint (ZIP, city, or state).
  demo         — area is a plain string area name matching data/services.json.

For the hospitals_us path, area should be a JSON-encoded dict with optional keys:
  {"state": "AL", "county": "COVINGTON", "city": "ANDALUSIA", "zip": "36420"}
A plain string that parses as 5 digits is treated as a ZIP; otherwise as a city.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Optional, Union

from src import audit
from src.advisor import RankedHospital, RankedResult, rank, rank_hospitals
from src.checks import final_check
from src.finder import HospitalCandidate, ServiceCandidate, find_hospitals, find_services
from src.gate import (
    GateClass,
    REPLY_OFFTOPIC,
    REPLY_SMALLTALK,
    REPLY_UNCLEAR,
    classify as gate_classify,
)
from src.intake import CaseResult, extract_case, get_followup_question
from src.locations import LocationResult, resolve_location
from src.safety import SafetyResult, check_red_flags

_MESSAGES_PATH = Path(__file__).parent.parent / "data" / "messages.json"
_MAX_CHECK_RETRIES = 2


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class SessionResult:
    """Full result returned by run_session()."""

    session_id: str
    tier: str                              # "emergency" | "urgent" | "routine"
    state: str                             # final state reached
    case: Optional[CaseResult]            # None on emergency (no intake needed)
    safety: Optional[SafetyResult]
    candidates: Union[list[ServiceCandidate], list[HospitalCandidate]]
    ranked: Union[list[RankedResult], list[RankedHospital]]
    answer_text: str                       # full grounding text used by final_check
    display_intro: str                     # short friendly sentence shown in the chat bubble
    check_passed: bool
    needs_human_review: bool
    handoff_summary: Optional[str]        # set only after user consent
    followup_questions_asked: int
    missing_fields: list[str]
    location: Optional[LocationResult]    # set when hospitals_us path is used
    gate_class: GateClass = "health"      # "health"|"smalltalk"|"offtopic"|"unclear"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_messages() -> dict:
    """Load data/messages.json and replace all placeholders at load time.

    Every string value in the messages dict has {emergency_number} and
    {emergency_region} replaced so that no raw placeholder can ever reach
    user-facing output.  The startup check verify_no_placeholders() confirms
    this guarantee.
    """
    with _MESSAGES_PATH.open(encoding="utf-8") as fh:
        raw = json.load(fh)["messages"]

    number = _emergency_number()
    region = _emergency_region()

    def _replace(obj: object) -> object:
        if isinstance(obj, str):
            return (
                obj
                .replace("{emergency_number}", number)
                .replace("{emergency_region}", region)
            )
        if isinstance(obj, dict):
            return {k: _replace(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_replace(i) for i in obj]
        return obj

    return _replace(raw)  # type: ignore[return-value]


# Code defaults — used when EMERGENCY_NUMBER / EMERGENCY_REGION are not set.
# These are the same sensible defaults shown in .env.example.
_DEMO_EMERGENCY_NUMBER = "911"
_DEMO_EMERGENCY_REGION = "DEMO REGION"


def _data_source() -> str:
    """Return 'hospitals_us' (default) or 'demo' from the DATA_SOURCE env var."""
    return os.environ.get("DATA_SOURCE", "hospitals_us").strip().lower()


def _emergency_number() -> str:
    """Read EMERGENCY_NUMBER from env; fallback to the code default (911)."""
    return os.environ.get("EMERGENCY_NUMBER", "").strip() or _DEMO_EMERGENCY_NUMBER


def _emergency_region() -> str:
    """Read EMERGENCY_REGION from env; fallback to a labelled demo value."""
    return os.environ.get("EMERGENCY_REGION", "").strip() or _DEMO_EMERGENCY_REGION


def _fill_placeholders(text: str) -> str:
    """Replace {emergency_number} and {emergency_region} placeholders."""
    return (
        text
        .replace("{emergency_number}", _emergency_number())
        .replace("{emergency_region}", _emergency_region())
    )


def verify_no_placeholders(messages: dict) -> None:
    """Startup check: raise RuntimeError if any message string still contains a raw placeholder.

    A raw placeholder looks like '[' followed by characters and ']' (e.g. the
    old '[DEMO-999]' sentinel), or a literal '{emergency_number}' that was not
    replaced.  Call this once at import time to catch misconfiguration early.

    Args:
        messages: The already-filled messages dict from _load_messages().

    Raises:
        RuntimeError: if any string value still contains a placeholder pattern.
    """
    import re as _re
    _BRACKET_PLACEHOLDER = _re.compile(r"\[DEMO")
    _BRACE_PLACEHOLDER   = _re.compile(r"\{emergency_(?:number|region)\}")

    def _check(obj: object, path: str) -> None:
        if isinstance(obj, str):
            if _BRACKET_PLACEHOLDER.search(obj) or _BRACE_PLACEHOLDER.search(obj):
                raise RuntimeError(
                    f"Unresolved placeholder in messages at {path!r}: {obj!r}. "
                    "Set EMERGENCY_NUMBER (and optionally EMERGENCY_REGION) in your .env file."
                )
        elif isinstance(obj, dict):
            for k, v in obj.items():
                _check(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _check(v, f"{path}[{i}]")

    _check(messages, "messages")


# Sentinel returned when a location string cannot be resolved without a state.
_NO_STATE_SENTINEL = "__no_state__"


def _parse_area_to_location(area: str) -> dict:
    """Parse the area string into keyword arguments for resolve_location.

    Accepts:
    - JSON dict: {"state": "AL", "county": "COVINGTON", "city": "ANDALUSIA", "zip": "36420"}
    - 5-digit string: treated as ZIP code (must include a state when possible, but
      a ZIP alone is enough to identify a unique location so it is accepted)
    - 2-letter state abbreviation: used as state filter.
    - Anything else: only safe if it looks like a 5-digit ZIP; bare city or county
      names without a state are rejected to prevent cross-state mixing.

    Returns a dict suitable for **resolve_location().  Returns the sentinel dict
    {"_no_state": True} when the input is a bare city/county name without a state,
    so that run_session can surface a clear message asking the user to pick a state.
    """
    area = area.strip()
    # Try JSON dict first.
    if area.startswith("{"):
        try:
            parsed = json.loads(area)
            kwargs = {k: v for k, v in parsed.items() if v}
            # A JSON dict with only city/county and no state/zip is still unsafe.
            if not kwargs.get("state") and not kwargs.get("zip"):
                return {"_no_state": True}
            return kwargs
        except (json.JSONDecodeError, TypeError):
            pass
    # 5-digit ZIP — accepted without a state (ZIP is unambiguous).
    if len(area) == 5 and area.isdigit():
        return {"zip": area}
    # Plain state abbreviation (2 letters).
    if len(area) == 2 and area.isalpha():
        return {"state": area.upper()}
    # Any other plain string (bare city name, county name, etc.) has no state
    # context — reject it to prevent cross-state mixing.
    return {"_no_state": True}


_DISTANT_HOSPITAL_THRESHOLD_MILES = 100.0


def _build_display_intro_hospitals(
    tier: str,
    location: Optional[LocationResult] = None,
) -> str:
    """Return a single friendly sentence for the chat bubble (hospitals_us path).

    Never includes individual hospital facts — those live in the cards.
    """
    if tier == "urgent":
        messages = _load_messages()
        urgent_msg = messages["urgent"]
        return urgent_msg["heading"] + " " + _fill_placeholders(urgent_msg["body"])

    # Derive a human-readable area label from location.
    area_label = ""
    if location is not None and location.rows:
        # Use the city/state of the first matched hospital row as a representative label.
        first = location.rows[0]
        city = first.get("CITY", "")
        state = first.get("STATE", "")
        if city and state:
            area_label = f" in {city.title()}, {state}"
        elif state:
            area_label = f" in {state}"

    return f"Based on what you described, here are the nearest suitable options{area_label}."


def _build_answer_text_hospitals(
    ranked: list[RankedHospital],
    tier: str,
    location: Optional[LocationResult] = None,
) -> str:
    """Build the recommendation answer text from ranked hospital results.

    Includes:
    - A widening notice if the location was broadened.
    - A distance warning if the nearest hospital is more than 100 miles away.
    - For urgent tier: the standard urgent header from messages.json.
    - Each hospital: name, ID, phone, validation year, 'call ahead to confirm'.
    - The reason text (which includes address, distance, type, val_year, call-ahead).
    - Never claims emergency department, 24-hour service or opening hours.
    """
    messages = _load_messages()
    lines: list[str] = []

    # Widening notice — always shown when the location was broadened.
    if location and location.notice:
        lines.append(f"*ℹ️ {location.notice}*")
        lines.append("")

    # 100-mile warning — shown when the nearest non-fallback result is far away.
    if ranked:
        top_non_fallback = next((r for r in ranked if not r.is_fallback), None)
        if top_non_fallback is not None:
            dist = top_non_fallback.hospital.distance_miles
            if dist is not None and dist > _DISTANT_HOSPITAL_THRESHOLD_MILES:
                lines.append(
                    f"⚠️ **The nearest hospital is approximately {dist:.0f} miles away.** "
                    "Consider calling 911 or asking someone to drive you if you cannot travel that distance safely."
                )
                lines.append("")

    if not ranked:
        body = messages["needs_human_review"]["body"]
        lines.append(_fill_placeholders(body))
        return "\n".join(lines)

    if tier == "urgent":
        urgent_msg = messages["urgent"]
        lines.append(urgent_msg["heading"])
        lines.append(_fill_placeholders(urgent_msg["body"]))
        lines.append(_fill_placeholders(urgent_msg["escalation_prompt"]))
        lines.append("")

    top = [r for r in ranked if not r.is_fallback]
    fallback = next((r for r in ranked if r.is_fallback), None)

    for r in top:
        h = r.hospital
        lines.append(
            f"**Option {r.rank}: {h.display_name or h.name.title()}** ({h.id})  "
            f"Phone: {h.phone}  "
            f"Validation year: {h.val_year}"
        )
        lines.append("*Call ahead to confirm availability before attending.*")
        lines.append(r.reason)
        lines.append("")

    if fallback:
        h = fallback.hospital
        lines.append(
            f"**Fallback: {h.display_name or h.name.title()}** ({h.id})  "
            f"Phone: {h.phone}  "
            f"Validation year: {h.val_year}"
        )
        lines.append("*Call ahead to confirm availability before attending.*")
        lines.append(fallback.reason)

    return "\n".join(lines)


def _build_answer_text(ranked: list[RankedResult], tier: str) -> str:
    """Build the recommendation answer text from ranked results (demo path)."""
    if not ranked:
        messages = _load_messages()
        body = messages["needs_human_review"]["body"]
        return _fill_placeholders(body)

    top = [r for r in ranked if not r.is_fallback]
    fallback = next((r for r in ranked if r.is_fallback), None)

    lines: list[str] = []
    if tier == "urgent":
        messages = _load_messages()
        urgent_msg = messages["urgent"]
        lines.append(urgent_msg["heading"])
        lines.append(_fill_placeholders(urgent_msg["body"]))
        lines.append(_fill_placeholders(urgent_msg["escalation_prompt"]))
        lines.append("")

    for r in top:
        svc = r.service
        lines.append(
            f"**Option {r.rank}: {svc.name}** ({svc.id})  "
            f"Phone: {svc.phone}  "
            f"Data last verified: {svc.last_verified}"
        )
        lines.append(r.reason)
        lines.append("")

    if fallback:
        svc = fallback.service
        lines.append(
            f"**Fallback: {svc.name}** ({svc.id})  "
            f"Phone: {svc.phone}  "
            f"Data last verified: {svc.last_verified}"
        )
        lines.append(fallback.reason)

    return "\n".join(lines)


def _build_handoff_summary(
    case: Optional[CaseResult],
    tier: str,
    ranked: Union[list[RankedResult], list[RankedHospital]],
) -> str:
    """Build a short handoff summary (only after explicit user consent).

    Format: one fact per line, no "demo" wording.
    """
    import datetime

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    # Derive location searched from case.area if available.
    location_searched = (case.area if case and case.area else "not specified")

    lines: list[str] = ["CareCompass summary"]
    lines.append(f"Date: {now}")
    lines.append(f"Location searched: {location_searched}")
    lines.append(f"Who: {case.who or 'not specified'}" if case else "Who: not specified")
    lines.append(f"Concern: {case.concern or 'not specified'}" if case else "Concern: not specified")
    lines.append(f"Duration: {case.duration or 'not specified'}" if case else "Duration: not specified")
    lines.append(f"Urgency: {tier}")

    if ranked:
        top = [r for r in ranked if not r.is_fallback]
        if top:
            r0 = top[0]
            # Support both RankedHospital and RankedResult.
            svc = getattr(r0, "hospital", None) or getattr(r0, "service", None)
            if svc is not None:
                name = getattr(svc, "display_name", None) or getattr(svc, "name", "") or "not specified"
                phone = getattr(svc, "phone", "") or "not specified"
                address_parts = [
                    p for p in (
                        getattr(svc, "address", ""),
                        getattr(svc, "city", "") or getattr(svc, "area", ""),
                        getattr(svc, "state", ""),
                    ) if p
                ]
                address = ", ".join(address_parts) or "not specified"
                val = getattr(svc, "val_year", None) or getattr(svc, "last_verified", "") or "not specified"
                lines.append(f"Suggested hospital: {name}")
                lines.append(f"Phone: {phone}")
                lines.append(f"Address: {address}")
                lines.append(f"Record validated: {val} (please call ahead to confirm)")
        else:
            lines.append("Suggested hospital: not available")
    else:
        lines.append("Suggested hospital: not available")

    lines.append("Generated by CareCompass. General guidance only, not medical advice.")
    return "\n".join(lines)


def _collect_cited_ids_hospitals(ranked: list[RankedHospital]) -> list[str]:
    return [r.hospital.id for r in ranked]


def _collect_cited_ids(ranked: list[RankedResult]) -> list[str]:
    return [r.service.id for r in ranked]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_session(
    user_text: str,
    area: str,
    session_id: Optional[str] = None,
) -> SessionResult:
    """Run the full 8-step pipeline for one user turn.

    The session is stateless: all prior-turn context must be passed via
    user_text (the caller is responsible for combining turns if needed).
    Followup questions are surfaced via the missing_fields of the returned
    SessionResult; the caller should prompt the user and call run_session
    again with the combined text.

    Args:
        user_text: Raw user input (may include follow-up answers appended).
        area: The area / location string selected by the user.
              For hospitals_us: JSON dict or plain ZIP/city/state.
              For demo: area name matching data/services.json.
        session_id: Optional existing session ID; a new UUID is created if None.

    Returns:
        SessionResult describing the final state and all intermediate results.
    """
    sid = session_id or audit.new_session_id()
    source = _data_source()

    # ── Step 1: describe (already done by caller; log the input) ──────────
    audit.log(sid, "describe", {"user_text": user_text, "area": area, "data_source": source})

    # ── Step 2: understand ────────────────────────────────────────────────
    case = extract_case(user_text, area)
    audit.log(sid, "understand", {
        "who": case.who,
        "concern": case.concern,
        "duration": case.duration,
        "severity_cues": case.severity_cues,
        "denied_symptoms": case.denied_symptoms,
        "missing_fields": case.missing_fields,
        "confidence": case.confidence,
    })

    followup_q = get_followup_question(case.missing_fields, 0)
    if followup_q:
        audit.log(sid, "followup", {"question": followup_q, "missing": case.missing_fields})

    # ── Step 3: safety ────────────────────────────────────────────────────
    safety_result = check_red_flags(user_text)
    audit.log(sid, "safety", {
        "tier": safety_result.tier,
        "hits": [h._asdict() for h in safety_result.hits],
    })

    if safety_result.tier == "emergency":
        messages = _load_messages()
        em = messages["emergency"]
        # body already has placeholders replaced by _load_messages()
        answer = f"🚨 **{em['heading']}**\n\n{em['body']}"
        audit.log(sid, "emergency_exit", {"answer_preview": answer[:120]})
        return SessionResult(
            session_id=sid,
            tier="emergency",
            state="emergency",
            case=case,
            safety=safety_result,
            candidates=[],
            ranked=[],
            answer_text=answer,
            # display_intro is the short chat-bubble sentence only.
            # The full alert box is rendered separately in app.py via st.error().
            display_intro=f"🚨 {em['heading']} See the alert above.",
            check_passed=True,   # emergency path bypasses check
            needs_human_review=False,
            handoff_summary=None,
            followup_questions_asked=0,
            missing_fields=case.missing_fields,
            location=None,
        )

    # ── Step 3b: gate — only runs when no red flag fired ──────────────────
    if safety_result.tier == "routine":
        gate_class = gate_classify(user_text)
        audit.log(sid, "gate", {
            "class": gate_class,
            "user_text_preview": user_text[:80],
        })

        if gate_class != "health":
            # Fill the emergency number placeholder in the reply templates.
            reply = {
                "smalltalk": REPLY_SMALLTALK,
                "offtopic":  REPLY_OFFTOPIC,
                "unclear":   REPLY_UNCLEAR,
            }[gate_class].replace("{emergency_number}", _emergency_number())

            return SessionResult(
                session_id=sid,
                tier="routine",
                state="gate",
                case=case,
                safety=safety_result,
                candidates=[],
                ranked=[],
                answer_text=reply,
                display_intro=reply,
                check_passed=True,
                needs_human_review=False,
                handoff_summary=None,
                followup_questions_asked=0,
                missing_fields=[],
                location=None,
                gate_class=gate_class,
            )
    else:
        gate_class = "health"

    # ── Step 4: find ──────────────────────────────────────────────────────
    location: Optional[LocationResult] = None

    if source == "hospitals_us":
        loc_kwargs = _parse_area_to_location(area)
        if loc_kwargs.get("_no_state"):
            # The area string is a bare city or county name without a state.
            # Return a clear message asking the user to choose a state rather
            # than silently querying hospitals across every state.
            messages = _load_messages()
            no_state_msg = messages.get(
                "no_state_in_area",
                "Please choose a state from the dropdown so we can find hospitals "
                "near you. A city or county name alone is not specific enough — "
                "the same name can appear in many states.",
            )
            audit.log(sid, "location_error", {"error": "no_state_in_area", "area": area})
            return SessionResult(
                session_id=sid,
                tier=safety_result.tier,
                state="no_state",
                case=case,
                safety=safety_result,
                candidates=[],
                ranked=[],
                answer_text=no_state_msg,
                display_intro=no_state_msg,
                check_passed=False,
                needs_human_review=False,
                handoff_summary=None,
                followup_questions_asked=1 if followup_q else 0,
                missing_fields=case.missing_fields,
                location=None,
            )
        try:
            location = resolve_location(**loc_kwargs)
        except ValueError as exc:
            audit.log(sid, "location_error", {"error": str(exc), "area": area})
            # Fall through to empty candidates → needs_human_review.
            location = None

        if location is not None:
            hospital_rows = find_hospitals(
                location.ref_lat,
                location.ref_lon,
                case,
                safety_result.tier,
                location.rows,
            )
        else:
            hospital_rows = []

        audit.log(sid, "find", {
            "tier": safety_result.tier,
            "candidate_count": len(hospital_rows),
            "candidate_ids": [h.id for h in hospital_rows],
            "widened": location.widened_from is not None if location else False,
        })

        # ── Step 5: compare / rank ────────────────────────────────────────
        ranked_hospitals = rank_hospitals(hospital_rows, safety_result.tier)
        audit.log(sid, "compare", {
            "ranked": [
                {"rank": r.rank, "id": r.hospital.id, "score": r.score, "fallback": r.is_fallback}
                for r in ranked_hospitals
            ]
        })

        # ── Step 6: final_check (≤2 retries) ─────────────────────────────
        answer_text = _build_answer_text_hospitals(ranked_hospitals, safety_result.tier, location)
        display_intro = _build_display_intro_hospitals(safety_result.tier, location)
        cited_ids = _collect_cited_ids_hospitals(ranked_hospitals)
        check_passed = False
        needs_review = False

        for attempt in range(_MAX_CHECK_RETRIES + 1):
            check_result = final_check(
                answer_text, cited_ids,
                tier=safety_result.tier,
                hospital_ids=cited_ids,
            )
            audit.log(sid, "final_check", {
                "attempt": attempt + 1,
                "passed": check_result.passed,
                "problems": check_result.problems,
            })
            if check_result.passed:
                check_passed = True
                break
            if attempt < _MAX_CHECK_RETRIES:
                answer_text = (
                    answer_text
                    + "\n\n*Note: some details could not be fully verified. "
                    "Please confirm directly with the hospital before attending.*"
                )
                audit.log(sid, "revise", {"attempt": attempt + 1})
            else:
                needs_review = True

        if needs_review:
            messages = _load_messages()
            if safety_result.tier in ("urgent", "emergency"):
                review_msg = messages["needs_human_review_urgent"]
                tier_label = safety_result.tier.upper()
                if safety_result.hits:
                    reason_parts = ", ".join(h.name.replace("_", " ") for h in safety_result.hits)
                else:
                    reason_parts = "symptoms described"
                handoff_line = _fill_placeholders(review_msg["handoff_prompt"])
                answer_text = (
                    f"**[{tier_label}]** {reason_parts.capitalize()}.\n\n"
                    + _fill_placeholders(review_msg["body"])
                    + f"\n\n{handoff_line}"
                )
            else:
                review_msg = messages["needs_human_review"]
                answer_text = (
                    f"**{review_msg['heading']}**\n\n"
                    + _fill_placeholders(review_msg["body"])
                )
            audit.log(sid, "needs_human_review", {"tier": safety_result.tier})

        # ── Step 7: present ───────────────────────────────────────────────
        audit.log(sid, "present", {"answer_preview": answer_text[:200]})

        state = "needs_human_review" if needs_review else "present"
        return SessionResult(
            session_id=sid,
            tier=safety_result.tier,
            state=state,
            case=case,
            safety=safety_result,
            candidates=hospital_rows,
            ranked=ranked_hospitals,
            answer_text=answer_text,
            display_intro=display_intro if not needs_review else answer_text,
            check_passed=check_passed,
            needs_human_review=needs_review,
            handoff_summary=None,
            followup_questions_asked=1 if followup_q else 0,
            missing_fields=case.missing_fields,
            location=location,
        )

    # ── Demo path ─────────────────────────────────────────────────────────
    candidates = find_services(case, safety_result.tier, safety_result.hits)
    audit.log(sid, "find", {
        "tier": safety_result.tier,
        "candidate_count": len(candidates),
        "candidate_ids": [c.id for c in candidates],
    })

    ranked = rank(candidates, safety_result.tier)
    audit.log(sid, "compare", {
        "ranked": [
            {"rank": r.rank, "id": r.service.id, "score": r.score, "fallback": r.is_fallback}
            for r in ranked
        ]
    })

    answer_text = _build_answer_text(ranked, safety_result.tier)
    cited_ids = _collect_cited_ids(ranked)
    check_passed = False
    needs_review = False

    for attempt in range(_MAX_CHECK_RETRIES + 1):
        check_result = final_check(answer_text, cited_ids, tier=safety_result.tier)
        audit.log(sid, "final_check", {
            "attempt": attempt + 1,
            "passed": check_result.passed,
            "problems": check_result.problems,
        })
        if check_result.passed:
            check_passed = True
            break
        if attempt < _MAX_CHECK_RETRIES:
            answer_text = (
                answer_text
                + "\n\n*Note: some details could not be fully verified. "
                "Please confirm directly with the service before attending.*"
            )
            audit.log(sid, "revise", {"attempt": attempt + 1})
        else:
            needs_review = True

    if needs_review:
        messages = _load_messages()
        if safety_result.tier in ("urgent", "emergency"):
            review_msg = messages["needs_human_review_urgent"]
            tier_label = safety_result.tier.upper()
            if safety_result.hits:
                reason_parts = ", ".join(h.name.replace("_", " ") for h in safety_result.hits)
            else:
                reason_parts = "symptoms described"
            handoff_line = _fill_placeholders(review_msg["handoff_prompt"])
            answer_text = (
                f"**[{tier_label}]** {reason_parts.capitalize()}.\n\n"
                + _fill_placeholders(review_msg["body"])
                + f"\n\n{handoff_line}"
            )
        else:
            review_msg = messages["needs_human_review"]
            answer_text = (
                f"**{review_msg['heading']}**\n\n"
                + _fill_placeholders(review_msg["body"])
            )
        audit.log(sid, "needs_human_review", {"tier": safety_result.tier})

    audit.log(sid, "present", {"answer_preview": answer_text[:200]})

    state = "needs_human_review" if needs_review else "present"
    return SessionResult(
        session_id=sid,
        tier=safety_result.tier,
        state=state,
        case=case,
        safety=safety_result,
        candidates=candidates,
        ranked=ranked,
        answer_text=answer_text,
        display_intro=answer_text,  # demo path: chat bubble = full answer (no separate cards)
        check_passed=check_passed,
        needs_human_review=needs_review,
        handoff_summary=None,
        followup_questions_asked=1 if followup_q else 0,
        missing_fields=case.missing_fields,
        location=None,
    )


def create_handoff_summary(session_result: SessionResult) -> str:
    """Create a handoff summary ONLY after explicit user consent.

    Args:
        session_result: The completed SessionResult from run_session().

    Returns:
        A plain-text summary string.
    """
    summary = _build_handoff_summary(
        session_result.case,
        session_result.tier,
        session_result.ranked,
    )
    audit.log(session_result.session_id, "handoff", {
        "consented": True,
        "summary_preview": summary[:120],
    })
    return summary
