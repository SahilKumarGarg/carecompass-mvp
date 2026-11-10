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
_FAVICON_SVG = _ASSETS / "favicon.svg"


def _data_uri(path: Path) -> str:
    """Return a data-URI string for the given image path."""
    mime = "image/png" if path.suffix == ".png" else "image/svg+xml"
    data = base64.b64encode(path.read_bytes()).decode()
    return f"data:{mime};base64,{data}"


def _logo_page_icon() -> str:
    """Return a data-URI string suitable for page_icon (favicon first), or the compass emoji."""
    for path in (_FAVICON_SVG, _LOGO_PNG, _LOGO_SVG):
        if path.exists():
            return _data_uri(path)
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
# Also: responsive stacking of header columns on narrow screens.
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

/* On narrow screens (<700 px) stack the three header items vertically */
@media (max-width: 699px) {
    .cc-header-row {
        display: flex !important;
        flex-direction: column !important;
        gap: 8px !important;
    }
}

/* ── Action button theming ──────────────────────────────────────────────── */
/* Base: full-width, 48 px high, 16 px bold */
.stButton > button[kind="primary"],
.stLinkButton > a[kind="primary"] {
    height: 48px !important;
    font-size: 16px !important;
    font-weight: 700 !important;
    border-radius: 8px !important;
}

/* Routine → teal fill, white text */
.cc-btn-routine .stButton > button,
.cc-btn-routine .stLinkButton > a {
    background-color: #0E7C86 !important;
    color: #FFFFFF !important;
    border: none !important;
}
.cc-btn-routine .stButton > button:hover,
.cc-btn-routine .stLinkButton > a:hover {
    background-color: #0B6870 !important;
}

/* Urgent → dark orange fill, white text */
.cc-btn-urgent .stButton > button,
.cc-btn-urgent .stLinkButton > a {
    background-color: #C05100 !important;
    color: #FFFFFF !important;
    border: none !important;
}
.cc-btn-urgent .stButton > button:hover,
.cc-btn-urgent .stLinkButton > a:hover {
    background-color: #9E4300 !important;
}

/* Emergency → red fill, white text */
.cc-btn-emergency .stButton > button,
.cc-btn-emergency .stLinkButton > a {
    background-color: #C62828 !important;
    color: #FFFFFF !important;
    border: none !important;
}
.cc-btn-emergency .stButton > button:hover,
.cc-btn-emergency .stLinkButton > a:hover {
    background-color: #A61F1F !important;
}
</style>
"""

st.markdown(_HIDE_CSS, unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Tier badge configuration
# ---------------------------------------------------------------------------

# Colours, icons and labels for each tier
_BADGE_CFG: dict[str, dict] = {
    "emergency": {
        "icon":    "🚨",
        "word":    "EMERGENCY",
        "bg":      "#C62828",
        "color":   "#FFFFFF",
        "border":  "#B71C1C",
    },
    "urgent": {
        "icon":    "⚠️",
        "word":    "URGENT",
        "bg":      "#FFF0CF",
        "color":   "#8A4B00",
        "border":  "#E6A817",
    },
    "routine": {
        "icon":    "✅",
        "word":    "ROUTINE",
        "bg":      "#DFF5E6",
        "color":   "#0B6E3B",
        "border":  "#A8D5B5",
    },
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
        "handoff_text":    None,
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
    st.session_state.handoff_text = None
    st.session_state.session_id = None
    st.session_state.step = "describe"


def _full_reset() -> None:
    """Start Over: clear conversation, result and badge; keep location selections."""
    st.session_state.messages = []
    st.session_state.session_result = None
    st.session_state.handoff_shown = False
    st.session_state.handoff_text = None
    st.session_state.session_id = None
    st.session_state.step = "describe"
    # Location keys (loc_state, loc_county, loc_city, loc_zip) are intentionally kept.


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
    return ", ".join(unique)


def _render_case_badge_html(result: SessionResult) -> None:
    """Render a large self-contained HTML badge showing tier, icon, word and reason.

    The badge is rendered via st.markdown(unsafe_allow_html=True) so that it
    can be placed inside any st.columns cell.  It is at least 56 px tall with
    20–22 px bold tier word.  Colour is never the only distinguishing cue:
    every tier also shows a distinct icon and the word ROUTINE / URGENT /
    EMERGENCY.
    """
    tier = result.tier
    cfg  = _BADGE_CFG.get(tier, _BADGE_CFG["routine"])
    why  = _build_why_line(result)

    icon_html = f'<span style="font-size:1.6rem;line-height:1" aria-hidden="true">{cfg["icon"]}</span>'
    word_html = (
        f'<span style="font-size:1.3rem;font-weight:700;letter-spacing:0.04em">'
        f'{cfg["word"]}</span>'
    )
    # Second line: why-reason (only if non-empty)
    reason_html = ""
    if why:
        reason_html = (
            f'<div style="font-size:0.78rem;font-weight:400;margin-top:4px;'
            f'opacity:0.85;line-height:1.3">{why}</div>'
        )

    html = (
        f'<div role="status" aria-label="{cfg["word"]}" style="'
        f'background:{cfg["bg"]};color:{cfg["color"]};'
        f'border:1.5px solid {cfg["border"]};border-radius:8px;'
        f'padding:10px 16px;min-height:56px;'
        f'display:flex;flex-direction:column;justify-content:center;'
        f'box-sizing:border-box">'
        f'  <div style="display:flex;align-items:center;gap:8px">'
        f'    {icon_html}'
        f'    {word_html}'
        f'  </div>'
        f'  {reason_html}'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def _render_logo_title_inline() -> None:
    """Render the logo (56 px) and 'CareCompass' title in dark teal on one line."""
    logo_path = _LOGO_PNG if _LOGO_PNG.exists() else (_LOGO_SVG if _LOGO_SVG.exists() else None)
    title_html = (
        '<span style="font-size:1.75rem;font-weight:700;color:#0B4F57;line-height:1.15">'
        'CareCompass</span>'
    )
    if logo_path is not None:
        uri = _data_uri(logo_path)
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:12px">'
            f'<img src="{uri}" style="height:56px;width:auto" alt="CareCompass logo"/>'
            f'{title_html}'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(title_html, unsafe_allow_html=True)


def _render_header_row(result: SessionResult | None) -> None:
    """Render the single header row with three columns.

    LEFT  (1.2): 'Start over' button — only when a conversation exists.
    MIDDLE (4):  Logo + 'CareCompass' title on one line.
    RIGHT  (2.6): Large case-type badge — only after a case has been assessed.
    """
    has_conversation = bool(st.session_state.get("messages"))
    col_left, col_mid, col_right = st.columns([1.2, 4, 2.6], vertical_alignment="center")

    with col_left:
        if has_conversation:
            if st.button(
                "Start over",
                icon=":material/restart_alt:",
                use_container_width=True,
                key="btn_start_over",
            ):
                _full_reset()
                st.rerun()

    with col_mid:
        _render_logo_title_inline()

    with col_right:
        if result is not None and result.state not in ("describe",):
            _render_case_badge_html(result)


def _format_distance_line(h: object, selected_city: str | None, selected_zip: str | None) -> str:
    """Return a human-readable distance string for a hospital.

    - If the hospital's city/state matches the user's selected city, returns
      "In CITY, ST" (never shows 0.0 miles).
    - If the user selected a ZIP and the hospital's ZIP matches, returns
      "In CITY, ST" likewise.
    - Otherwise returns "About X miles from the centre of your selected area
      (approximate)".
    - Falls back to empty string when distance_miles is None.

    Args:
        h:             HospitalCandidate instance.
        selected_city: The city the user chose in the location dropdowns (may be None).
        selected_zip:  The ZIP the user entered (may be None).
    """
    hosp_city: str = getattr(h, "city", "") or ""
    hosp_state: str = getattr(h, "state", "") or ""
    hosp_zip: str = getattr(h, "zip", "") or ""
    dist: float | None = getattr(h, "distance_miles", None)

    # Determine if the hospital is "in" the user's selected area.
    city_match = (
        selected_city
        and hosp_city.upper() == selected_city.upper()
    )
    zip_match = (
        selected_zip
        and hosp_zip == selected_zip
    )

    if city_match or zip_match:
        label = f"{hosp_city.title()}, {hosp_state}" if hosp_city and hosp_state else (hosp_city.title() or hosp_state)
        return f"📍 In {label}"

    if dist is None:
        return ""

    return f"📍 About {dist:.0f} miles from the centre of your selected area (approximate)"


def _render_hospital_card(r: object, *, top: bool, selected_city: str | None = None, selected_zip: str | None = None) -> None:
    """Render one hospital result as a styled card.

    Top card: white bg, 14 px radius, soft shadow, 20 px padding, 4 px teal left accent border.
    Other cards: same minus the left accent.
    Material icons are used for hospital/phone/address/call-ahead lines.
    Type/trauma/beds are small teal-tinted rounded badges.
    Call-ahead note is amber (#8A4B00 on #FFF4DB).

    Args:
        r:             A RankedHospital instance.
        top:           True for the primary (larger) card, False for secondary cards.
        selected_city: City name from the user's location selection (for distance wording).
        selected_zip:  ZIP code from the user's location selection (for distance wording).
    """
    h = r.hospital  # type: ignore[attr-defined]

    name = h.display_name or h.name.title()
    if r.is_fallback:  # type: ignore[attr-defined]
        name += " (alternative option)"

    left_border = "border-left:4px solid #0E7C86;" if top else ""
    card_style = (
        f"background:#FFFFFF;border:1px solid #D5E6E9;border-radius:14px;"
        f"box-shadow:0 2px 8px rgba(14,124,134,0.08);padding:20px 22px;"
        f"margin-bottom:16px;{left_border}"
    )
    heading_size = "1.15rem" if top else "1rem"

    # Build inner HTML
    lines: list[str] = []

    # Name / heading
    lines.append(
        f'<div style="font-size:{heading_size};font-weight:700;color:#16313A;'
        f'margin-bottom:10px">{name}</div>'
    )

    # Distance line
    dist_line = _format_distance_line(h, selected_city, selected_zip)
    if dist_line:
        # strip the leading emoji — we use Material icon instead
        dist_text = dist_line.lstrip("📍").strip()
        lines.append(
            f'<div style="margin-bottom:6px">'
            f'<span style="vertical-align:middle;font-size:1rem" class="material-icons-round">location_on</span>'
            f' <span style="vertical-align:middle">{dist_text}</span></div>'
        )

    # Phone
    if h.phone:
        phone_digits = "".join(ch for ch in h.phone if ch.isdigit())
        lines.append(
            f'<div style="margin-bottom:6px">'
            f'<span style="vertical-align:middle;font-size:1rem" class="material-icons-round">call</span>'
            f' <a href="tel:{phone_digits}" style="vertical-align:middle;font-weight:600;'
            f'color:#0E7C86;text-decoration:none">{h.phone}</a></div>'
        )

    # Address
    addr_parts = [p for p in (h.address, h.city, h.state, h.zip) if p]
    if addr_parts:
        lines.append(
            f'<div style="margin-bottom:6px">'
            f'<span style="vertical-align:middle;font-size:1rem" class="material-icons-round">local_hospital</span>'
            f' <span style="vertical-align:middle">{", ".join(addr_parts)}</span></div>'
        )

    # Teal-tinted badges: type, trauma, beds
    badge_parts: list[str] = []
    if h.type:
        badge_parts.append(h.type.replace("_", " ").title())
    if h.trauma:
        badge_parts.append(f"Trauma: {h.trauma}")
    if h.beds:
        badge_parts.append(f"{h.beds} beds")
    if badge_parts:
        badges_html = " ".join(
            f'<span style="display:inline-block;background:#E4F1F3;color:#0B4F57;'
            f'border:1px solid #A8D5D9;border-radius:20px;padding:2px 10px;'
            f'font-size:0.75rem;font-weight:500;margin-right:4px">{p}</span>'
            for p in badge_parts
        )
        lines.append(f'<div style="margin-bottom:10px;margin-top:6px">{badges_html}</div>')

    # Call-ahead note (amber)
    val_text = f"Record validated {h.val_year}. " if h.val_year else ""
    lines.append(
        f'<div style="margin-top:8px;background:#FFF4DB;border-radius:6px;padding:6px 10px;'
        f'font-size:0.82rem;color:#8A4B00">'
        f'<span style="vertical-align:middle;font-size:0.95rem" class="material-icons-round">info</span>'
        f' <span style="vertical-align:middle">{val_text}Call ahead to confirm hours and availability.</span>'
        f'</div>'
    )

    # Dataset ID (small muted, always visible without expander)
    lines.append(
        f'<div style="margin-top:8px;font-size:0.73rem;color:#57606a">ID: {h.id}</div>'
    )

    inner = "\n".join(lines)
    # We embed Material Icons via a Google Fonts link in the card HTML itself,
    # avoiding a global <link> tag (which Streamlit sometimes strips).
    material_link = (
        '<link rel="stylesheet" href="https://fonts.googleapis.com/icon?family=Material+Icons+Round"/>'
    )
    st.markdown(
        f'{material_link}<div style="{card_style}">{inner}</div>',
        unsafe_allow_html=True,
    )


def _render_hospital_cards(result: SessionResult, selected_city: str | None = None, selected_zip: str | None = None) -> None:
    """Render the hospitals_us ranked results as structured cards.

    Shows the routine advisory in an info box above the cards (once only),
    then one large card for the top result and smaller cards for the rest.

    Args:
        result:        The completed SessionResult.
        selected_city: City name from the user's location selection.
        selected_zip:  ZIP code from the user's location selection.
    """
    from src.advisor import _ROUTINE_ADVISORY  # import here to keep function self-contained

    if not result.ranked:
        return

    # Routine advisory box — shown once above all cards, not repeated in each card.
    if result.tier == "routine":
        st.info(_ROUTINE_ADVISORY)

    top_list    = [r for r in result.ranked if not r.is_fallback]
    others_list = [r for r in result.ranked if r.is_fallback]

    if top_list:
        _render_hospital_card(top_list[0], top=True, selected_city=selected_city, selected_zip=selected_zip)
    for r in top_list[1:]:
        _render_hospital_card(r, top=False, selected_city=selected_city, selected_zip=selected_zip)
    for r in others_list:
        _render_hospital_card(r, top=False, selected_city=selected_city, selected_zip=selected_zip)


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
    # Collect prior user turns only — exclude the current prompt which the caller
    # has already appended to st.session_state.messages before calling this function.
    # Without the [:-1] slice the current text would appear twice in combined_text,
    # causing the concern field to be duplicated in the handoff summary.
    prior_messages = st.session_state.messages[:-1]
    prior_user = " ".join(
        m["content"] for m in prior_messages if m["role"] == "user"
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
# Footer
# ---------------------------------------------------------------------------

def _render_footer() -> None:
    """Render a small grey disclaimer footer at the bottom of the page."""
    number = os.environ.get("EMERGENCY_NUMBER", "").strip() or "911"
    st.markdown("---")
    st.caption(
        f"CareCompass gives general guidance, not medical advice. "
        f"In an emergency call {number}.",
    )


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

def main() -> None:
    _init_state()

    result: SessionResult | None = st.session_state.session_result

    # ── Header row (Start over | Logo+title | Badge) ──────────────────────
    # The header is built first so the badge is always at the top of the page.
    _render_header_row(result)

    st.markdown(
        '<p style="color:#57606a;font-size:0.95rem;margin-top:2px;margin-bottom:16px">'
        "Describe a health concern and I'll help you find the right local service."
        '</p>',
        unsafe_allow_html=True,
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

    # Refresh result reference after potential auto-clear above.
    result = st.session_state.session_result

    # ── Recommendation display ────────────────────────────────────────────
    if result is not None and result.state not in ("describe",):

        if result.tier == "emergency":
            st.error(result.answer_text)

        elif result.needs_human_review:
            st.warning(result.answer_text)

        else:
            # hospitals_us path: RankedHospital has .hospital attribute.
            # demo path: RankedResult has .service attribute.
            is_hospitals = result.ranked and hasattr(result.ranked[0], "hospital")
            if is_hospitals:
                _render_hospital_cards(result, selected_city=city, selected_zip=zip_)
            else:
                _render_demo_card(result)

        # ── Single action button per tier (case actions only) ─────────────
        st.divider()
        if not st.session_state.handoff_shown:
            spec = get_button_spec(result.tier, result)

            # Wrap in a tier-specific div so our CSS classes can target it.
            btn_class = f"cc-btn-{result.tier}"
            st.markdown(f'<div class="{btn_class}">', unsafe_allow_html=True)

            if spec["kind"] == "link":
                st.link_button(
                    spec["label"],
                    url=spec["url"],
                    use_container_width=True,
                    type="primary",
                )

            else:
                # "consent" → show button, generate summary on click
                if st.button(spec["label"], key="action_consent", use_container_width=True, type="primary"):
                    summary = create_handoff_summary(result)
                    st.session_state.handoff_shown = True
                    st.session_state.handoff_text = summary
                    st.rerun()

            st.markdown('</div>', unsafe_allow_html=True)

        elif hasattr(st.session_state, "handoff_text") and st.session_state.handoff_text:
            # Display the summary with the copy icon; wrap_lines prevents horizontal overflow.
            st.caption("Summary — copy with the icon in the top-right corner.")
            st.code(st.session_state.handoff_text, language=None, wrap_lines=True)

    # ── Footer ────────────────────────────────────────────────────────────
    _render_footer()

    # ── Chat input ────────────────────────────────────────────────────────
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
                # Store display_intro in the chat history (short friendly sentence).
                # answer_text is kept for final_check grounding — not shown in the bubble.
                st.session_state.messages.append(
                    {"role": "assistant", "content": result.display_intro}
                )
                st.rerun()


if __name__ == "__main__":
    main()
