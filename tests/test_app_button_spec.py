"""Tests for app.get_button_spec().

get_button_spec is a pure function — it reads from the SessionResult and the
EMERGENCY_NUMBER env var. No Streamlit imports needed; we import only from app
and from src.orchestrator for the SessionResult type.

Rules under test:
  - emergency  → kind=link, label contains the emergency number, url=tel:<number>
  - emergency  → falls back to 911 when EMERGENCY_NUMBER env var is unset
  - emergency  → uses EMERGENCY_NUMBER when env var is set
  - urgent     → kind=link, label contains top hospital name, url is a Google Maps URL
  - urgent     → works with RankedHospital (hospitals_us path, .hospital attr)
  - urgent     → works with RankedResult   (demo path, .service attr)
  - urgent     → falls back to generic search when ranked is empty
  - urgent     → skips fallback entries (is_fallback=True) when picking the top
  - routine    → kind=consent, no url key
  - routine    → label is the approved consent string
"""

from __future__ import annotations

import dataclasses
import os
from typing import Optional
from unittest.mock import patch

import pytest

from app import get_button_spec
from src.orchestrator import SessionResult


# ---------------------------------------------------------------------------
# Minimal stub objects — only the attrs get_button_spec accesses
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class _StubHospital:
    display_name: str = "Andalusia Health"
    name: str         = "ANDALUSIA HEALTH"
    address: str      = "849 S THREE NOTCH ST"
    city: str         = "ANDALUSIA"
    state: str        = "AL"


@dataclasses.dataclass
class _StubService:
    name: str = "Greenfield GP Clinic"
    area: str = "Greenfield"
    # no display_name, no address, no state — mirrors demo ServiceCandidate


@dataclasses.dataclass
class _RankedHospital:
    rank: int
    hospital: _StubHospital
    score: float  = 0.9
    reason: str   = ""
    is_fallback: bool = False


@dataclasses.dataclass
class _RankedService:
    rank: int
    service: _StubService
    score: float  = 0.8
    reason: str   = ""
    is_fallback: bool = False


def _make_result(
    tier: str = "routine",
    ranked: list | None = None,
    needs_human_review: bool = False,
) -> SessionResult:
    return SessionResult(
        session_id="test-session",
        tier=tier,
        state="present",
        case=None,
        safety=None,
        candidates=[],
        ranked=ranked or [],
        answer_text="",
        display_intro="",
        check_passed=True,
        needs_human_review=needs_human_review,
        handoff_summary=None,
        followup_questions_asked=0,
        missing_fields=[],
        location=None,
    )


# ---------------------------------------------------------------------------
# Emergency tier
# ---------------------------------------------------------------------------

class TestGetButtonSpecEmergency:
    def test_kind_is_link(self) -> None:
        spec = get_button_spec("emergency", _make_result("emergency"))
        assert spec["kind"] == "link"

    def test_default_number_is_911(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("EMERGENCY_NUMBER", None)
            spec = get_button_spec("emergency", _make_result("emergency"))
        assert "911" in spec["label"]
        assert spec["url"] == "tel:911"

    def test_uses_env_var_number(self) -> None:
        with patch.dict(os.environ, {"EMERGENCY_NUMBER": "999"}):
            spec = get_button_spec("emergency", _make_result("emergency"))
        assert "999" in spec["label"]
        assert spec["url"] == "tel:999"

    def test_label_starts_with_call(self) -> None:
        spec = get_button_spec("emergency", _make_result("emergency"))
        assert spec["label"].startswith("Call ")

    def test_url_is_tel_scheme(self) -> None:
        spec = get_button_spec("emergency", _make_result("emergency"))
        assert spec["url"].startswith("tel:")


# ---------------------------------------------------------------------------
# Urgent tier
# ---------------------------------------------------------------------------

class TestGetButtonSpecUrgent:
    def test_kind_is_link(self) -> None:
        ranked = [_RankedHospital(rank=1, hospital=_StubHospital())]
        spec = get_button_spec("urgent", _make_result("urgent", ranked=ranked))
        assert spec["kind"] == "link"

    def test_label_contains_hospital_name(self) -> None:
        ranked = [_RankedHospital(rank=1, hospital=_StubHospital(display_name="City Hospital"))]
        spec = get_button_spec("urgent", _make_result("urgent", ranked=ranked))
        assert "City Hospital" in spec["label"]

    def test_url_is_google_maps(self) -> None:
        ranked = [_RankedHospital(rank=1, hospital=_StubHospital())]
        spec = get_button_spec("urgent", _make_result("urgent", ranked=ranked))
        assert spec["url"].startswith("https://www.google.com/maps/search/")

    def test_url_contains_hospital_name_encoded(self) -> None:
        ranked = [_RankedHospital(rank=1, hospital=_StubHospital(display_name="Andalusia Health"))]
        spec = get_button_spec("urgent", _make_result("urgent", ranked=ranked))
        assert "Andalusia" in spec["url"]

    def test_works_with_demo_service_attr(self) -> None:
        """Demo path (RankedResult) uses .service, not .hospital."""
        ranked = [_RankedService(rank=1, service=_StubService(name="Greenfield GP Clinic"))]
        spec = get_button_spec("urgent", _make_result("urgent", ranked=ranked))
        assert spec["kind"] == "link"
        assert "Greenfield GP Clinic" in spec["label"]

    def test_fallback_only_returns_generic_url(self) -> None:
        """When all ranked items have is_fallback=True, fall back to generic search."""
        ranked = [_RankedHospital(rank=1, hospital=_StubHospital(), is_fallback=True)]
        spec = get_button_spec("urgent", _make_result("urgent", ranked=ranked))
        assert "hospital" in spec["url"].lower()

    def test_empty_ranked_returns_generic_url(self) -> None:
        spec = get_button_spec("urgent", _make_result("urgent", ranked=[]))
        assert spec["kind"] == "link"
        assert "hospital" in spec["url"].lower()

    def test_skips_fallback_to_find_top(self) -> None:
        """is_fallback=True entries must be skipped; second entry (non-fallback) wins."""
        ranked = [
            _RankedHospital(rank=1, hospital=_StubHospital(display_name="Skip Me"), is_fallback=True),
            _RankedHospital(rank=2, hospital=_StubHospital(display_name="Choose Me"), is_fallback=False),
        ]
        spec = get_button_spec("urgent", _make_result("urgent", ranked=ranked))
        assert "Choose Me" in spec["label"]
        assert "Skip Me" not in spec["label"]


# ---------------------------------------------------------------------------
# Routine tier
# ---------------------------------------------------------------------------

class TestGetButtonSpecRoutine:
    def test_kind_is_consent(self) -> None:
        spec = get_button_spec("routine", _make_result("routine"))
        assert spec["kind"] == "consent"

    def test_no_url_key(self) -> None:
        spec = get_button_spec("routine", _make_result("routine"))
        assert "url" not in spec

    def test_label_is_approved_string(self) -> None:
        spec = get_button_spec("routine", _make_result("routine"))
        assert spec["label"] == "Create a summary to share with my provider"


# ---------------------------------------------------------------------------
# Page config — browser tab title must not contain "Demo"
# ---------------------------------------------------------------------------

class TestPageConfig:
    def test_page_title_contains_no_demo(self) -> None:
        """page_title must not include the word 'Demo' in any capitalisation."""
        import ast, pathlib
        source = pathlib.Path(__file__).parent.parent.joinpath("app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "set_page_config"
            ):
                for kw in node.keywords:
                    if kw.arg == "page_title" and isinstance(kw.value, ast.Constant):
                        title = kw.value.value
                        assert "demo" not in title.lower(), (
                            f"page_title {title!r} still contains 'Demo'. "
                            "Remove it from the set_page_config call."
                        )
                        return
        pytest.fail("set_page_config(page_title=...) not found in app.py")
