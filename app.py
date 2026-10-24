"""CareCompass MVP — Streamlit chat application.

Run with:  streamlit run app.py

All data is synthetic demo data. This app does not provide medical advice.
"""

from __future__ import annotations

import streamlit as st

from src.orchestrator import SessionResult, create_handoff_summary, run_session

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="CareCompass (Demo)",
    page_icon="🧭",
    layout="centered",
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_AREAS = ["Greenfield", "Riverside", "Northgate", "Eastfield", "Westbury"]

_STEP_LABELS = {
    "describe":           "1 · Describe",
    "understand":         "2 · Understand",
    "safety":             "3 · Safety check",
    "find":               "4 · Find services",
    "compare":            "5 · Compare",
    "final_check":        "6 · Final check",
    "present":            "7 · Recommendation",
    "handoff":            "8 · Handoff",
    "emergency":          "⚠ Emergency",
    "needs_human_review": "⚠ Needs review",
}

_TIER_BADGE: dict[str, str] = {
    "emergency": "🔴 Emergency",
    "urgent":    "🟠 Urgent",
    "routine":   "🟢 Routine",
}

_URGENT_ACTION = "Please go to the first option without delay."

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------

def _init_state() -> None:
    defaults: dict = {
        "messages": [],           # list of {"role": "user"|"assistant", "content": str}
        "session_result": None,   # SessionResult | None
        "step": "describe",       # current pipeline step label
        "area": _AREAS[0],
        "handoff_shown": False,
        "session_id": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def _render_step_status(current_step: str) -> None:
    """Show a compact step indicator in the sidebar."""
    steps = list(_STEP_LABELS.values())
    current_label = _STEP_LABELS.get(current_step, current_step)
    for label in steps:
        icon = "✅" if label == current_label else "⬜"
        st.sidebar.markdown(f"{icon} {label}")


def _render_recommendation_card(result: SessionResult) -> None:
    """Display the top recommendation as a styled card."""
    top = [r for r in result.ranked if not r.is_fallback]
    if not top:
        return
    best = top[0]
    svc = best.service
    with st.container(border=True):
        st.markdown(f"### {svc.name}")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"**Type:** {svc.type.replace('_', ' ').title()}")
            st.markdown(f"**Area:** {svc.area}")
            st.markdown(f"**Phone:** {svc.phone}")
        with col2:
            st.markdown(f"**Hours:** {svc.hours}")
            if svc.distance_km is not None:
                st.markdown(
                    f"**Distance:** ~{svc.distance_km:.1f} km "
                    f"(~{svc.travel_min_estimate:.0f} min est.)"
                )
            else:
                st.markdown("**Distance:** Online — no travel needed")
            st.markdown(f"**Last verified:** {svc.last_verified}")
        st.markdown(f"*{best.reason}*")


def _build_why_line(result: SessionResult) -> str:
    """Return a short 'Why' string listing the rule names that fired, or empty string."""
    if result.safety is None or not result.safety.hits:
        return ""
    names = [h.name.replace("_", " ") for h in result.safety.hits]
    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            unique.append(n)
    return "Why: " + ", ".join(unique)


def _render_tier_banner(result: SessionResult) -> None:
    """Render the tier badge, Why line, and (for urgent) the action prompt."""
    tier = result.tier
    badge = _TIER_BADGE.get(tier, tier)
    why = _build_why_line(result)

    if tier == "emergency":
        badge_md = f"**{badge}**"
        if why:
            st.error(f"{badge_md}  \n{why}")
        else:
            st.error(badge_md)
    elif tier == "urgent":
        badge_md = f"**{badge}**"
        lines = [badge_md]
        if why:
            lines.append(why)
        lines.append(f"⚠️ {_URGENT_ACTION}")
        st.warning("  \n".join(lines))
    else:
        badge_md = f"**{badge}**"
        if why:
            st.info(f"{badge_md}  \n{why}")
        else:
            st.info(badge_md)


def _render_service_table(result: SessionResult) -> None:
    """Show all ranked options in an expander."""
    if not result.ranked:
        return
    with st.expander("All options", expanded=False):
        for r in result.ranked:
            svc = r.service
            tag = " *(fallback)*" if r.is_fallback else f" · score {r.score:.2f}"
            st.markdown(
                f"**{r.rank}. {svc.name}**{tag}  \n"
                f"{svc.phone} · {svc.hours} · verified {svc.last_verified}"
            )


# ---------------------------------------------------------------------------
# Message processing
# ---------------------------------------------------------------------------

def _process_user_input(user_text: str, area: str) -> None:
    """Run the pipeline and update session state."""
    st.session_state.step = "understand"

    # Combine with any previous user turns in this session for context.
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

    # ── Sidebar ──────────────────────────────────────────────────────────
    with st.sidebar:
        st.title("🧭 CareCompass")
        st.caption("Demo data only. Not medical advice.")
        st.divider()

        area = st.selectbox(
            "Your area",
            _AREAS,
            index=_AREAS.index(st.session_state.area),
            key="area_select",
        )
        st.session_state.area = area
        st.divider()

        st.markdown("**Pipeline steps**")
        _render_step_status(st.session_state.step)
        st.divider()

        if st.button("🔄 Start over", use_container_width=True):
            for k in ["messages", "session_result", "step", "handoff_shown", "session_id"]:
                st.session_state[k] = (
                    [] if k == "messages"
                    else None if k in ("session_result", "session_id")
                    else False if k == "handoff_shown"
                    else "describe"
                )
            st.rerun()

    # ── Main panel ───────────────────────────────────────────────────────
    st.title("CareCompass")
    st.caption(
        "Describe a health concern and I'll help you find the right local service. "
        "**Demo data only — not real medical advice.**"
    )

    # Display chat history.
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    result: SessionResult | None = st.session_state.session_result

    # ── Recommendation display ───────────────────────────────────────────
    if result is not None and result.state not in ("describe",):
        tier_badge = _TIER_BADGE.get(result.tier, result.tier)

        _render_tier_banner(result)

        if result.tier == "emergency":
            st.error(result.answer_text)

        elif result.needs_human_review:
            st.warning(result.answer_text)

        else:
            _render_recommendation_card(result)
            _render_service_table(result)

            # Full answer text in expander.
            with st.expander("Full recommendation text", expanded=False):
                st.markdown(result.answer_text)

            # ── Handoff consent gate ─────────────────────────────────────
            if not st.session_state.handoff_shown:
                st.divider()
                st.markdown("### Would you like a summary to share with a healthcare professional?")
                col_yes, col_no, _ = st.columns([1, 1, 3])
                with col_yes:
                    if st.button("✅ Yes", key="handoff_yes"):
                        summary = create_handoff_summary(result)
                        st.session_state.handoff_shown = True
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": f"**Summary created:**\n\n```\n{summary}\n```",
                        })
                        st.rerun()
                with col_no:
                    if st.button("❌ No", key="handoff_no"):
                        st.session_state.handoff_shown = True
                        st.session_state.messages.append({
                            "role": "assistant",
                            "content": "No summary created. You can start a new search any time.",
                        })
                        st.rerun()

    # ── Chat input ───────────────────────────────────────────────────────
    if prompt := st.chat_input("Describe your concern here…"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Checking…"):
                _process_user_input(prompt, st.session_state.area)

            result = st.session_state.session_result
            if result is None:
                st.markdown("Something went wrong. Please try again.")
            else:
                # Add assistant reply to history.
                reply = result.answer_text[:500] + (
                    "…" if len(result.answer_text) > 500 else ""
                )
                st.session_state.messages.append({"role": "assistant", "content": reply})
                st.rerun()


if __name__ == "__main__":
    main()
