"""tests/test_summarize_user_test.py

Tests for scripts/summarize_user_test.py using small fake CSVs via tmp_path.
No real results file is required — the tests are fully self-contained.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make scripts/ importable via the project root (conftest.py already adds root
# to sys.path, so we only need to add scripts/ separately).
_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from summarize_user_test import Row, _parse_csv, summarise, format_report  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEADER = "participant,case,completed,minutes,know_next,clear,comment\n"


def _write_csv(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "results.csv"
    p.write_text(_HEADER + body, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _parse_csv — happy path
# ---------------------------------------------------------------------------

class TestParseCsvHappyPath:
    def test_returns_parse_result(self, tmp_path: Path) -> None:
        p = _write_csv(tmp_path, "P1,WC1,yes,3,5,4,Good response\n")
        result = _parse_csv(p)
        assert result.rows
        assert not result.errors

    def test_single_row_fields(self, tmp_path: Path) -> None:
        p = _write_csv(tmp_path, "Alice,WC1,yes,2.5,4,3,Fine\n")
        row = _parse_csv(p).rows[0]
        assert row.participant == "Alice"
        assert row.case == "WC1"
        assert row.completed is True
        assert row.minutes == 2.5
        assert row.know_next == 4
        assert row.clear == 3
        assert row.comment == "Fine"

    def test_completed_no(self, tmp_path: Path) -> None:
        p = _write_csv(tmp_path, "P1,WA1,no,4,3,3,\n")
        row = _parse_csv(p).rows[0]
        assert row.completed is False

    def test_empty_comment_allowed(self, tmp_path: Path) -> None:
        p = _write_csv(tmp_path, "P1,WA1,yes,2,5,5,\n")
        result = _parse_csv(p)
        assert not result.errors
        assert result.rows[0].comment == ""

    def test_multiple_rows(self, tmp_path: Path) -> None:
        body = (
            "P1,WA1,yes,2,5,5,Good\n"
            "P1,WB1,no,6,3,3,\n"
            "P2,WA1,yes,3,4,4,\n"
        )
        result = _parse_csv(_write_csv(tmp_path, body))
        assert len(result.rows) == 3
        assert not result.errors

    def test_case_insensitive_yes(self, tmp_path: Path) -> None:
        p = _write_csv(tmp_path, "P1,WC1,YES,3,5,5,\n")
        assert _parse_csv(p).rows[0].completed is True

    def test_case_insensitive_no(self, tmp_path: Path) -> None:
        p = _write_csv(tmp_path, "P1,WC1,No,3,5,5,\n")
        assert _parse_csv(p).rows[0].completed is False

    def test_header_only_returns_no_rows_no_errors(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.csv"
        p.write_text(_HEADER, encoding="utf-8")
        result = _parse_csv(p)
        assert result.rows == []
        assert result.errors == []


# ---------------------------------------------------------------------------
# _parse_csv — validation errors
# ---------------------------------------------------------------------------

class TestParseCsvErrors:
    def test_missing_file_gives_error(self, tmp_path: Path) -> None:
        result = _parse_csv(tmp_path / "does_not_exist.csv")
        assert result.errors
        assert any("not found" in e.lower() for e in result.errors)

    def test_missing_column_gives_error(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.csv"
        p.write_text("participant,case,completed\nP1,WA1,yes\n")
        result = _parse_csv(p)
        assert result.errors
        assert any("missing columns" in e.lower() for e in result.errors)

    def test_completed_not_yes_no(self, tmp_path: Path) -> None:
        p = _write_csv(tmp_path, "P1,WA1,maybe,3,4,4,\n")
        result = _parse_csv(p)
        assert result.errors
        assert any("completed" in e for e in result.errors)
        assert result.rows == []   # bad row excluded

    def test_know_next_out_of_range(self, tmp_path: Path) -> None:
        p = _write_csv(tmp_path, "P1,WA1,yes,3,6,4,\n")
        result = _parse_csv(p)
        assert result.errors
        assert any("know_next" in e for e in result.errors)

    def test_clear_out_of_range(self, tmp_path: Path) -> None:
        p = _write_csv(tmp_path, "P1,WA1,yes,3,4,0,\n")
        result = _parse_csv(p)
        assert result.errors
        assert any("clear" in e for e in result.errors)

    def test_minutes_zero_is_invalid(self, tmp_path: Path) -> None:
        p = _write_csv(tmp_path, "P1,WA1,yes,0,4,4,\n")
        result = _parse_csv(p)
        assert result.errors
        assert any("minutes" in e for e in result.errors)

    def test_minutes_negative_is_invalid(self, tmp_path: Path) -> None:
        p = _write_csv(tmp_path, "P1,WA1,yes,-1,4,4,\n")
        result = _parse_csv(p)
        assert result.errors
        assert any("minutes" in e for e in result.errors)

    def test_minutes_non_numeric_is_invalid(self, tmp_path: Path) -> None:
        p = _write_csv(tmp_path, "P1,WA1,yes,abc,4,4,\n")
        result = _parse_csv(p)
        assert result.errors
        assert any("minutes" in e for e in result.errors)

    def test_empty_participant_is_invalid(self, tmp_path: Path) -> None:
        p = _write_csv(tmp_path, ",WA1,yes,3,4,4,\n")
        result = _parse_csv(p)
        assert result.errors
        assert any("participant" in e for e in result.errors)

    def test_bad_row_excluded_good_row_kept(self, tmp_path: Path) -> None:
        body = (
            "P1,WA1,yes,3,4,4,Good\n"     # valid
            "P2,WB1,maybe,2,4,4,Bad\n"    # invalid completed
        )
        result = _parse_csv(_write_csv(tmp_path, body))
        assert len(result.rows) == 1
        assert result.rows[0].participant == "P1"
        assert result.errors  # error for the bad row

    def test_error_mentions_row_number(self, tmp_path: Path) -> None:
        p = _write_csv(tmp_path, "P1,WA1,maybe,3,4,4,\n")
        result = _parse_csv(p)
        assert any("row 2" in e.lower() for e in result.errors)


# ---------------------------------------------------------------------------
# summarise
# ---------------------------------------------------------------------------

class TestSummarise:
    def _rows(self) -> list[Row]:
        return [
            Row("P1", "WA1", True,  2.0, 5, 4, ""),
            Row("P1", "WB1", False, 5.0, 3, 3, "Confusing"),
            Row("P2", "WA1", True,  3.0, 4, 5, ""),
            Row("P2", "WB1", True,  4.0, 4, 4, "Good"),
        ]

    def test_empty_rows_gives_zero_participants(self) -> None:
        s = summarise([])
        assert s["participants"] == 0
        assert s["total_tasks"] == 0

    def test_participants_counted_by_unique_name(self) -> None:
        assert summarise(self._rows())["participants"] == 2

    def test_total_tasks(self) -> None:
        assert summarise(self._rows())["total_tasks"] == 4

    def test_completed_count(self) -> None:
        assert summarise(self._rows())["completed_count"] == 3

    def test_completed_pct(self) -> None:
        assert summarise(self._rows())["completed_pct"] == 75.0

    def test_avg_know_next(self) -> None:
        # (5+3+4+4)/4 = 16/4 = 4.0
        assert summarise(self._rows())["avg_know_next"] == 4.0

    def test_avg_clear(self) -> None:
        # (4+3+5+4)/4 = 16/4 = 4.0
        assert summarise(self._rows())["avg_clear"] == 4.0

    def test_avg_minutes(self) -> None:
        # (2+5+3+4)/4 = 14/4 = 3.5
        assert summarise(self._rows())["avg_minutes"] == 3.5

    def test_only_non_empty_comments_returned(self) -> None:
        coms = summarise(self._rows())["comments"]
        assert len(coms) == 2
        texts = [c["comment"] for c in coms]
        assert "Confusing" in texts
        assert "Good" in texts

    def test_comment_includes_participant_and_case(self) -> None:
        coms = summarise(self._rows())["comments"]
        rec = next(c for c in coms if c["comment"] == "Confusing")
        assert rec["participant"] == "P1"
        assert rec["case"] == "WB1"


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------

class TestFormatReport:
    def _stats(self) -> dict:
        rows = [
            Row("P1", "WA1", True,  2.0, 5, 4, ""),
            Row("P2", "WA1", False, 3.0, 3, 3, "Bit unclear"),
        ]
        return summarise(rows)

    def test_contains_participants(self) -> None:
        out = format_report(self._stats(), [])
        assert "Participants" in out
        assert "2" in out

    def test_contains_completed_fraction(self) -> None:
        out = format_report(self._stats(), [])
        assert "1 of 2" in out

    def test_contains_pct(self) -> None:
        out = format_report(self._stats(), [])
        assert "50%" in out

    def test_contains_avg_know_next(self) -> None:
        out = format_report(self._stats(), [])
        assert "know" in out.lower() or "4.0" in out

    def test_contains_avg_clear(self) -> None:
        out = format_report(self._stats(), [])
        assert "3.5" in out or "clear" in out.lower()

    def test_contains_avg_minutes(self) -> None:
        out = format_report(self._stats(), [])
        assert "2.5" in out

    def test_comment_shown_with_participant(self) -> None:
        out = format_report(self._stats(), [])
        assert "Bit unclear" in out
        assert "P2" in out

    def test_validation_errors_shown(self) -> None:
        out = format_report(self._stats(), ["Row 2: 'completed' bad"])
        assert "VALIDATION ERRORS" in out
        assert "Row 2" in out

    def test_no_data_message_when_empty(self) -> None:
        out = format_report(summarise([]), [])
        assert "No valid data" in out
