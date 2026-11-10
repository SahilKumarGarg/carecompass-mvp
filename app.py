"""CareCompass MVP — Streamlit chat application.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import urllib.parse

import streamlit as st

from src.orchestrator import SessionResult, create_handoff_summary, run_session
from src.locations import list_states, list_counties, list_cities

# ---------------------------------------------------------------------------
# Page config — sidebar collapsed, toolbar minimal (also in config.toml)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Logo — prefer assets/logo.png, fall back to assets/logo.svg
# ---------------------------------------------------------------------------

_ASSETS = Path(__file__).parent / "assets"
_LOGO_PNG = _ASSETS / "logo.png"
_LOGO_SVG = _ASSETS / "logo.svg"


def _logo_page_icon() -> str:
    """Return a data-URI string suitable for page_icon, or the compass emoji."""
    for path in (_LOGO_PNG, _LOGO_SVG):
        if path.exists():
            mime = "image/png" if path.suffix == ".png" else "image/svg+xml"
            data = base64.b64encode(path.read_bytes()).decode()
            return f"data:{mime};base64,{data}"
    return "🧭"


st.set_page_config(
    page_title="CareCompass",
    page_icon=_logo_page_icon(),
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


def _render_hospital_card(r: object, *, top: bool) -> None:
    """Render one hospital result as a structured card.

    Args:
        r:   A RankedHospital instance.
        top: True for the primary (larger) card, False for secondary cards.
    """
    h = r.hospital  # type: ignore[attr-defined]

    name        = h.display_name or h.name.title()
    heading_tag = "###" if top else "####"

    with st.container(border=True):
        label = name
        if r.is_fallback:  # type: ignore[attr-defined]
            label += " *(alternative option)*"
        st.markdown(f"{heading_tag} {label}")

        # ── Distance and drive time ────────────────────────────────────
        if h.distance_miles is not None:
            drive = (
                f"  · ~{h.travel_min_estimate:.0f} min drive (estimate, 40 mph)"
                if h.travel_min_estimate is not None
                else ""
            )
            st.markdown(f"📍 **~{h.distance_miles:.1f} miles away**{drive}")

        # ── Phone as a clickable tel: link ─────────────────────────────
        if h.phone:
            phone_digits = "".join(ch for ch in h.phone if ch.isdigit())
            st.markdown(f"📞 [**{h.phone}**](tel:{phone_digits})")

        # ── Address ────────────────────────────────────────────────────
        addr_parts = [p for p in (h.address, h.city, h.state, h.zip) if p]
        if addr_parts:
            st.markdown("🏥 " + ", ".join(addr_parts))

        # ── Type, trauma, beds ─────────────────────────────────────────
        meta: list[str] = [h.type.replace("_", " ").title()]
        if h.trauma:
            meta.append(f"Trauma: {h.trauma}")
        if h.beds:
            meta.append(f"{h.beds} beds")
        st.markdown("  ·  ".join(meta))

        # ── Validation notice ──────────────────────────────────────────
        val_text = f"Record validated {h.val_year} — " if h.val_year else ""
        st.caption(f"{val_text}call ahead to confirm.")

        # ── Details expander: ID only ──────────────────────────────────
        with st.expander("Details", expanded=False):
            st.caption(f"Dataset ID: {h.id}")


def _render_hospital_cards(result: SessionResult) -> None:
    """Render the hospitals_us ranked results as structured cards.

    Shows the routine advisory in an info box above the cards, then one
    large card for the top result and smaller cards for the rest.
    """
    from src.advisor import _ROUTINE_ADVISORY  # import here to keep function self-contained

    if not result.ranked:
        return

    # Routine advisory box — shown once above all cards.
    if result.tier == "routine":
        st.info(_ROUTINE_ADVISORY)

    top_list    = [r for r in result.ranked if not r.is_fallback]
    others_list = [r for r in result.ranked if r.is_fallback]

    if top_list:
        _render_hospital_card(top_list[0], top=True)
    for r in top_list[1:]:
        _render_hospital_card(r, top=False)
    for r in others_list:
        _render_hospital_card(r, top=False)


def _render_demo_card(result: SessionResult) -> None:
    """Display the top demo-path recommendation as a styled card."""
    top = [r for r in result.ranked if not r.is_fallback]
    if not top:
        return
    best = top[0]
    svc = getattr(best, "service", None)
    if svc is None:
        return

    with st.container(border=True):
        st.markdown(f"### {svc.name}")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"**Type:** {svc.type.replace('_', ' ').title()}")
            if svc.area:
                st.markdown(f"**Area:** {svc.area}")
            if svc.phone:
                phone_digits = "".join(ch for ch in svc.phone if ch.isdigit())
                st.markdown(f"**Phone:** [{svc.phone}](tel:{phone_digits})")
        with col2:
            if svc.hours:
                st.markdown(f"**Hours:** {svc.hours}")
            if svc.distance_km is not None:
                travel = svc.travel_min_estimate
                st.markdown(
                    f"**Distance:** ~{svc.distance_km:.1f} km"
                    + (f"  (~{travel:.0f} min est.)" if travel else "")
                )
            else:
                st.markdown("**Distance:** Online — no travel needed")
            val = getattr(svc, "last_verified", "")
            if val:
                st.markdown(f"**Verified:** {val}")
        st.markdown(f"*{best.reason}*")


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

def _render_logo_title() -> None:
    """Render the logo and app title on the same line, vertically centred."""
    logo_path = _LOGO_PNG if _LOGO_PNG.exists() else (_LOGO_SVG if _LOGO_SVG.exists() else None)
    if logo_path is not None:
        mime = "image/png" if logo_path.suffix == ".png" else "image/svg+xml"
        data = base64.b64encode(logo_path.read_bytes()).decode()
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:0.25rem">'
            f'<img src="data:{mime};base64,{data}" style="height:56px;width:auto" alt="CareCompass logo"/>'
            f'<span style="font-size:2rem;font-weight:700;line-height:1.2">CareCompass</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.title("CareCompass")


def _render_footer() -> None:
    """Render a small grey disclaimer footer at the bottom of the page."""
    number = os.environ.get("EMERGENCY_NUMBER", "").strip() or "911"
    st.markdown("---")
    st.caption(
        f"CareCompass gives general guidance, not medical advice. "
        f"In an emergency call {number}.",
    )


def main() -> None:
    _init_state()

    # ── Header ────────────────────────────────────────────────────────────
    _render_logo_title()
    st.caption(
        "Describe a health concern and I'll help you find the right local service."
    )

    # ── Location inputs ───────────────────────────────────────────────────
    with st.expander("📍 Set your location", expanded=True):
        state, county, city, zip_, loc_notice = _render_location_inputs()

    if loc_notice:
        st.caption(f"ℹ️ {loc_notice}")

    # Build a JSON-encoded location dict so run_session has full context.
    # All four fields are included; _parse_area_to_location unpacks them.
    loc_dict: dict = {}
    if state:
        loc_dict["state"] = state
    if county:
        loc_dict["county"] = county
    if city:
        loc_dict["city"] = city
    if zip_:
        loc_dict["zip"] = zip_
    area: str = json.dumps(loc_dict) if loc_dict else "Unknown"

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
            # hospitals_us path: RankedHospital has .hospital attribute.
            # demo path: RankedResult has .service attribute.
            is_hospitals = result.ranked and hasattr(result.ranked[0], "hospital")
            if is_hospitals:
                _render_hospital_cards(result)
            else:
                _render_demo_card(result)

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
    # ── Footer ────────────────────────────────────────────────────────────
    _render_footer()

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
                st.session_state.messages.append(
                    {"role": "assistant", "content": result.answer_text}
                )
                st.rerun()


if __name__ == "__main__":
    main()
