"""CareCompass MVP — Streamlit chat application.

Run with:  streamlit run app.py

All data is synthetic demo data. This app does not provide medical advice.
"""

from __future__ import annotations

import os
import urllib.parse

import streamlit as st

from src.orchestrator import SessionResult, create_handoff_summary, run_session
from src.locations import list_states, list_counties, list_cities

# ---------------------------------------------------------------------------
# Page config — sidebar collapsed, toolbar minimal (also in config.toml)
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="CareCompass (Demo)",
    page_icon="🧭",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# CSS: hide sidebar toggle, sidebar itself, top toolbar, status widget,
# decoration bar, main menu and footer.
# ---------------------------------------------------------------------------

_HIDE_CSS = """
<style>
/* Sidebar and its toggle button */
[data-testid="collapsedControl"] { display: none !important; }
section[data-testid="stSidebar"]  { display: none !important; }

/* Top toolbar, status popup, decoration bar, main menu, footer */
[data-testid="stToolbar"]     { display: none !important; }
[data-testid="stStatusWidget"]{ display: none !important; }
[data-testid="stDecoration"]  { display: none !important; }
#MainMenu                     { display: none !important; }
footer                        { display: none !important; }
</style>
"""

st.markdown(_HIDE_CSS, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Tier badge map
# ---------------------------------------------------------------------------

_TIER_BADGE: dict[str, str] = {
    "emergency": "🔴 Emergency",
    "urgent":    "🟠 Urgent",
    "routine":   "🟢 Routine",
}

_URGENT_ACTION = "Please go to the first option without delay."

# ---------------------------------------------------------------------------
# Button-spec helper — pure function, fully testable
# ---------------------------------------------------------------------------

def get_button_spec(tier: str, result: SessionResult) -> dict:
    """Return a dict describing the single action button to show for this tier.

    Keys:
        kind  : "link" for st.link_button, "consent" for the summary button.
        label : Button label text.
        url   : URL string (only when kind=="link").

    Args:
        tier:   "emergency" | "urgent" | "routine"
        result: The completed SessionResult.

    Returns:
        A dict with "kind", "label", and optionally "url".
    """
    if tier == "emergency":
        number = os.environ.get("EMERGENCY_NUMBER", "").strip() or "911"
        return {
            "kind": "link",
            "label": f"Call {number} now",
            "url": f"tel:{number}",
        }

    if tier == "urgent":
        # Find the top non-fallback ranked hospital.
        top = next(
            (r for r in result.ranked if not r.is_fallback),
            None,
        )
        if top is not None:
            # RankedHospital (hospitals_us path) has .hospital; RankedResult (demo) has .service.
            svc = getattr(top, "hospital", None) or getattr(top, "service", None)
            if svc is not None:
                name    = getattr(svc, "display_name", None) or getattr(svc, "name", "")
                address = getattr(svc, "address", "")
                city    = getattr(svc, "city",    "") or getattr(svc, "area", "")
                state   = getattr(svc, "state",   "")
                query   = ", ".join(p for p in (name, address, city, state) if p)
                maps_url = (
                    "https://www.google.com/maps/search/?api=1&query="
                    + urllib.parse.quote(query)
                )
                return {
                    "kind": "link",
                    "label": f"Get directions to {name}",
                    "url": maps_url,
                }
        # Fallback if no top result.
        return {
            "kind": "link",
            "label": "Search for nearby hospitals",
            "url": "https://www.google.com/maps/search/?api=1&query=hospital+near+me",
        }

    # routine
    return {
        "kind": "consent",
        "label": "Create a summary to share with my provider",
    }


# ---------------------------------------------------------------------------
# Location input helpers — cached with st.cache_data
# ---------------------------------------------------------------------------

@st.cache_data
def _cached_states() -> list[str]:
    return list_states()


@st.cache_data
def _cached_counties(state: str) -> list[tuple[str, str]]:
    return list_counties(state)


@st.cache_data
def _cached_cities(state: str, county: str | None) -> list[str]:
    return list_cities(state, county)


def _render_location_inputs() -> tuple[str | None, str | None, str | None, str | None, str]:
    """Render State/County/City/ZIP inputs.  Returns (state, county, city, zip_, notice)."""
    states = _cached_states()

    col1, col2 = st.columns(2)
    with col1:
        state_choice = st.selectbox(
            "State *",
            options=["— choose state —"] + states,
            key="loc_state",
            help="Required unless you enter a ZIP code.",
        )
        state: str | None = state_choice if state_choice != "— choose state —" else None

    with col2:
        zip_raw = st.text_input(
            "ZIP code (optional)",
            max_chars=5,
            key="loc_zip",
            placeholder="e.g. 36420",
        ).strip()
        zip_: str | None = None
        zip_error = ""
        if zip_raw:
            if len(zip_raw) == 5 and zip_raw.isdigit():
                zip_ = zip_raw
            else:
                zip_error = "ZIP must be exactly 5 digits."

    if zip_error:
        st.warning(zip_error)

    # County — depends on State
    county: str | None = None
    county_name: str | None = None
    city: str | None = None

    if state:
        counties = _cached_counties(state)
        county_names = [name for _, name in counties]

        col3, col4 = st.columns(2)
        with col3:
            county_choice = st.selectbox(
                "County (optional)",
                options=["— any county —"] + county_names,
                key="loc_county",
            )
            county_name = county_choice if county_choice != "— any county —" else None

        with col4:
            cities = _cached_cities(state, county_name)
            city_choice = st.selectbox(
                "City (optional)",
                options=["— any city —"] + cities,
                key="loc_city",
            )
            city = city_choice if city_choice != "— any city —" else None

    # Build a human-readable notice of which level is being used.
    notice = _location_level_notice(state, county_name, city, zip_)
    return state, county_name, city, zip_, notice


def _location_level_notice(
    state: str | None,
    county: str | None,
    city: str | None,
    zip_: str | None,
) -> str:
    """Return a short string describing the active location level."""
    if zip_:
        parts = [f"ZIP {zip_}"]
        if state:
            parts.append(state)
        return "Location: " + ", ".join(parts)
    if city and state:
        parts = [city, state]
        if county:
            parts = [city, county, state]
        return "Location: " + ", ".join(parts)
    if county and state:
        return f"Location: {county} County, {state}"
    if state:
        return f"Location: {state} (state-wide)"
    return ""


# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------

def _init_state() -> None:
    defaults: dict = {
        "messages":        [],
        "session_result":  None,
        "step":            "describe",
        "handoff_shown":   False,
        "session_id":      None,
        "_last_area":      None,   # track location changes to auto-clear results
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _reset_session() -> None:
    """Clear the previous result so a new message or location change starts fresh."""
    st.session_state.session_result = None
    st.session_state.handoff_shown = False
    st.session_state.session_id = None
    st.session_state.step = "describe"


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def _build_why_line(result: SessionResult) -> str:
    """Return a short 'Why' string listing the rule names that fired, or empty string."""
    if result.safety is None or not result.safety.hits:
        return ""
    names = [h.name.replace("_", " ") for h in result.safety.hits]
    seen: set[str] = set()
    unique: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            unique.append(n)
    return "Why: " + ", ".join(unique)


def _render_tier_banner(result: SessionResult) -> None:
    """Render the tier badge and one-line reason (Why line)."""
    tier  = result.tier
    badge = _TIER_BADGE.get(tier, tier)
    why   = _build_why_line(result)

    if tier == "emergency":
        badge_md = f"**{badge}**"
        st.error(f"{badge_md}  \n{why}" if why else badge_md)

    elif tier == "urgent":
        badge_md = f"**{badge}**"
        lines = [badge_md]
        if why:
            lines.append(why)
        lines.append(f"⚠️ {_URGENT_ACTION}")
        st.warning("  \n".join(lines))

    else:
        badge_md = f"**{badge}**"
        st.info(f"{badge_md}  \n{why}" if why else badge_md)


def _render_recommendation_card(result: SessionResult) -> None:
    """Display the top recommendation as a styled card (non-emergency/urgent)."""
    # hospitals_us path: ranked holds RankedHospital objects.
    # demo path: ranked holds RankedResult objects.
    top = [r for r in result.ranked if not r.is_fallback]
    if not top:
        return
    best = top[0]
    # Support both RankedHospital (.hospital) and RankedResult (.service).
    svc = getattr(best, "hospital", None) or getattr(best, "service", None)
    if svc is None:
        return

    with st.container(border=True):
        name = getattr(svc, "display_name", None) or getattr(svc, "name", "")
        st.markdown(f"### {name}")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"**Type:** {svc.type.replace('_', ' ').title()}")
            city  = getattr(svc, "city",  None) or getattr(svc, "area", "")
            state = getattr(svc, "state", "")
            if city:
                st.markdown(f"**Location:** {city}{', ' + state if state else ''}")
            phone = getattr(svc, "phone", "")
            if phone:
                st.markdown(f"**Phone:** {phone}")
        with col2:
            hours = getattr(svc, "hours", None)
            if hours:
                st.markdown(f"**Hours:** {hours}")
            dist_mi = getattr(svc, "distance_miles", None)
            dist_km = getattr(svc, "distance_km",    None)
            travel  = getattr(svc, "travel_min_estimate", None)
            if dist_mi is not None:
                st.markdown(
                    f"**Distance:** ~{dist_mi:.1f} mi "
                    f"(~{travel:.0f} min est.)" if travel else f"~{dist_mi:.1f} mi"
                )
            elif dist_km is not None:
                st.markdown(
                    f"**Distance:** ~{dist_km:.1f} km "
                    f"(~{travel:.0f} min est.)" if travel else f"~{dist_km:.1f} km"
                )
            else:
                st.markdown("**Distance:** Online — no travel needed")
            val = getattr(svc, "val_year", None) or getattr(svc, "last_verified", "")
            if val:
                st.markdown(f"**Verified:** {val}")
        st.markdown(f"*{best.reason}*")


def _render_service_table(result: SessionResult) -> None:
    """Show all ranked options in an expander."""
    if not result.ranked:
        return
    with st.expander("All options", expanded=False):
        for r in result.ranked:
            svc = getattr(r, "hospital", None) or getattr(r, "service", None)
            if svc is None:
                continue
            name  = getattr(svc, "display_name", None) or getattr(svc, "name", "")
            phone = getattr(svc, "phone", "")
            val   = getattr(svc, "val_year", None) or getattr(svc, "last_verified", "")
            tag   = " *(fallback)*" if r.is_fallback else f" · score {r.score:.2f}"
            st.markdown(
                f"**{r.rank}. {name}**{tag}  \n"
                f"{phone} · verified {val}"
            )


# ---------------------------------------------------------------------------
# Message processing
# ---------------------------------------------------------------------------

def _process_user_input(user_text: str, area: str) -> None:
    """Run the pipeline and update session state."""
    st.session_state.step = "understand"
    prior_user = " ".join(
        m["content"] for m in st.session_state.messages if m["role"] == "user"
    )
    combined_text = (prior_user + " " + user_text).strip() if prior_user else user_text

    result: SessionResult = run_session(
        user_text=combined_text,
        area=area,
        session_id=st.session_state.session_id,
    )
    st.session_state.session_id = result.session_id
    st.session_state.session_result = result
    st.session_state.step = result.state


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

def main() -> None:
    _init_state()

    # ── Header ────────────────────────────────────────────────────────────
    st.title("CareCompass")
    st.caption(
        "Describe a health concern and I'll help you find the right local service. "
        "**Demo data only — not real medical advice.**"
    )

    # ── Location inputs ───────────────────────────────────────────────────
    with st.expander("📍 Set your location", expanded=True):
        state, county, city, zip_, loc_notice = _render_location_inputs()

    if loc_notice:
        st.caption(f"ℹ️ {loc_notice}")

    # Derive area string passed to run_session.
    # Prefer ZIP, then city, then county, then state.
    area: str = zip_ or city or county or state or "Unknown"

    # ── Auto-clear on location change ─────────────────────────────────────
    # When the user changes their location, the old result belongs to a
    # different area. Clear it silently so the next message starts fresh.
    if st.session_state._last_area != area:
        if st.session_state.session_result is not None:
            _reset_session()
            st.session_state.messages = []
        st.session_state._last_area = area

    # ── Chat history ──────────────────────────────────────────────────────
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    result: SessionResult | None = st.session_state.session_result

    # ── Recommendation display ────────────────────────────────────────────
    if result is not None and result.state not in ("describe",):

        _render_tier_banner(result)

        if result.tier == "emergency":
            st.error(result.answer_text)

        elif result.needs_human_review:
            st.warning(result.answer_text)

        else:
            _render_recommendation_card(result)
            _render_service_table(result)

            with st.expander("Full recommendation text", expanded=False):
                st.markdown(result.answer_text)

        # ── Single action button per tier ─────────────────────────────────
        if not st.session_state.handoff_shown:
            st.divider()
            spec = get_button_spec(result.tier, result)

            if spec["kind"] == "link":
                st.link_button(spec["label"], url=spec["url"], use_container_width=False)

            else:
                # "consent" → show button, generate summary on click
                if st.button(spec["label"], key="action_consent"):
                    summary = create_handoff_summary(result)
                    st.session_state.handoff_shown = True
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": f"**Summary created:**\n\n```\n{summary}\n```",
                    })
                    st.rerun()

    # ── Chat input ────────────────────────────────────────────────────────
    # A new message automatically replaces the previous result via
    # _process_user_input; no explicit "Start over" button is needed.
    if prompt := st.chat_input("Describe your concern here…"):
        # Require at least state or ZIP before proceeding.
        if not state and not zip_:
            st.warning("Please select a State or enter a ZIP code before describing your concern.")
            st.stop()

        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Checking…"):
                _process_user_input(prompt, area)

            result = st.session_state.session_result
            if result is None:
                st.markdown("Something went wrong. Please try again.")
            else:
                reply = result.answer_text[:500] + (
                    "…" if len(result.answer_text) > 500 else ""
                )
                st.session_state.messages.append({"role": "assistant", "content": reply})
                st.rerun()


if __name__ == "__main__":
    main()
