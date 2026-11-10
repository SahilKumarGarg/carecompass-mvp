"""Tests for scripts/prepare_hospitals.py.

Uses a tiny in-memory fixture CSV written to a tmp_path so no real raw data
is needed.  Covers:
  - CLOSED hospital removed
  - -999 BEDS → empty
  - ZIP with leading zero preserved
  - NOT AVAILABLE telephone → empty
  - VAL_DATE / SOURCEDATE truncated to year
  - Rows without valid coordinates dropped
  - Duplicate IDs: only first row kept
  - NAME upper-cased; display_name title-cased
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

# Import the functions under test directly from the script.
# conftest.py adds the project root to sys.path, so this import works.
import importlib.util, sys

_SCRIPT = Path(__file__).parent.parent / "scripts" / "prepare_hospitals.py"
_spec = importlib.util.spec_from_file_location("prepare_hospitals", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

prepare = _mod.prepare


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_HEADER = [
    "X", "Y", "OBJECTID", "ID", "NAME", "ADDRESS", "CITY", "STATE", "ZIP",
    "ZIP4", "TELEPHONE", "TYPE", "STATUS", "POPULATION", "COUNTY",
    "COUNTYFIPS", "COUNTRY", "LATITUDE", "LONGITUDE", "NAICS_CODE",
    "NAICS_DESC", "SOURCE", "SOURCEDATE", "VAL_METHOD", "VAL_DATE",
    "WEBSITE", "STATE_ID", "ALT_NAME", "ST_FIPS", "OWNER", "TTL_STAFF",
    "BEDS", "TRAUMA", "HELIPAD",
]

_BASE_ROW: dict[str, str] = {
    "X": "-86.49", "Y": "31.29", "OBJECTID": "1",
    "ID": "0001111111", "NAME": "test hospital",
    "ADDRESS": "1 main st", "CITY": "springfield", "STATE": "al",
    "ZIP": "06001", "ZIP4": "",
    "TELEPHONE": "(334) 555-0000",
    "TYPE": "GENERAL ACUTE CARE", "STATUS": "OPEN",
    "POPULATION": "100",
    "COUNTY": "covington", "COUNTYFIPS": "01039",
    "COUNTRY": "USA", "LATITUDE": "31.295", "LONGITUDE": "-86.495",
    "NAICS_CODE": "622110", "NAICS_DESC": "", "SOURCE": "",
    "SOURCEDATE": "2021/10/19 00:00:00+00",
    "VAL_METHOD": "", "VAL_DATE": "2014/02/10 00:00:00+00",
    "WEBSITE": "http://example.com", "STATE_ID": "", "ALT_NAME": "",
    "ST_FIPS": "01", "OWNER": "NON-PROFIT", "TTL_STAFF": "",
    "BEDS": "100", "TRAUMA": "LEVEL III", "HELIPAD": "Y",
}


def _make_row(**overrides: str) -> dict[str, str]:
    row = dict(_BASE_ROW)
    row.update(overrides)
    return row


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_HEADER, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestClosedHospitalRemoved:
    def test_closed_not_in_output(self, tmp_path: Path) -> None:
        raw = tmp_path / "Hospitals.csv"
        out = tmp_path / "hospitals_clean.csv"
        _write_csv(raw, [
            _make_row(ID="OPEN001", STATUS="OPEN"),
            _make_row(ID="CLOS001", STATUS="CLOSED"),
        ])
        stats = prepare(raw_path=raw, out_path=out)
        assert stats["closed_removed"] == 1
        assert stats["rows_out"] == 1
        rows = list(csv.DictReader(out.open(encoding="utf-8")))
        ids = [r["ID"] for r in rows]
        assert "OPEN001" in ids
        assert "CLOS001" not in ids


class TestBedsClean:
    def test_minus_999_becomes_empty(self, tmp_path: Path) -> None:
        raw = tmp_path / "Hospitals.csv"
        out = tmp_path / "hospitals_clean.csv"
        _write_csv(raw, [_make_row(ID="B001", BEDS="-999")])
        prepare(raw_path=raw, out_path=out)
        rows = list(csv.DictReader(out.open(encoding="utf-8")))
        assert rows[0]["BEDS"] == ""

    def test_valid_beds_preserved(self, tmp_path: Path) -> None:
        raw = tmp_path / "Hospitals.csv"
        out = tmp_path / "hospitals_clean.csv"
        _write_csv(raw, [_make_row(ID="B002", BEDS="250")])
        prepare(raw_path=raw, out_path=out)
        rows = list(csv.DictReader(out.open(encoding="utf-8")))
        assert rows[0]["BEDS"] == "250"


class TestZipLeadingZero:
    def test_leading_zero_preserved(self, tmp_path: Path) -> None:
        raw = tmp_path / "Hospitals.csv"
        out = tmp_path / "hospitals_clean.csv"
        _write_csv(raw, [_make_row(ID="Z001", ZIP="06001")])
        prepare(raw_path=raw, out_path=out)
        rows = list(csv.DictReader(out.open(encoding="utf-8")))
        assert rows[0]["ZIP"] == "06001"

    def test_short_zip_padded(self, tmp_path: Path) -> None:
        raw = tmp_path / "Hospitals.csv"
        out = tmp_path / "hospitals_clean.csv"
        _write_csv(raw, [_make_row(ID="Z002", ZIP="1234")])
        prepare(raw_path=raw, out_path=out)
        rows = list(csv.DictReader(out.open(encoding="utf-8")))
        assert rows[0]["ZIP"] == "01234"


class TestNotAvailableTelephone:
    def test_not_available_becomes_empty(self, tmp_path: Path) -> None:
        raw = tmp_path / "Hospitals.csv"
        out = tmp_path / "hospitals_clean.csv"
        _write_csv(raw, [_make_row(ID="T001", TELEPHONE="NOT AVAILABLE")])
        prepare(raw_path=raw, out_path=out)
        rows = list(csv.DictReader(out.open(encoding="utf-8")))
        assert rows[0]["TELEPHONE"] == ""


class TestDateTruncation:
    def test_val_date_year_only(self, tmp_path: Path) -> None:
        raw = tmp_path / "Hospitals.csv"
        out = tmp_path / "hospitals_clean.csv"
        _write_csv(raw, [_make_row(ID="D001", VAL_DATE="2017/06/15 00:00:00+00")])
        prepare(raw_path=raw, out_path=out)
        rows = list(csv.DictReader(out.open(encoding="utf-8")))
        assert rows[0]["VAL_DATE"] == "2017"

    def test_sourcedate_year_only(self, tmp_path: Path) -> None:
        raw = tmp_path / "Hospitals.csv"
        out = tmp_path / "hospitals_clean.csv"
        _write_csv(raw, [_make_row(ID="D002", SOURCEDATE="2019/03/01 00:00:00+00")])
        prepare(raw_path=raw, out_path=out)
        rows = list(csv.DictReader(out.open(encoding="utf-8")))
        assert rows[0]["SOURCEDATE"] == "2019"


class TestInvalidCoordinatesDropped:
    def test_empty_lat_dropped(self, tmp_path: Path) -> None:
        raw = tmp_path / "Hospitals.csv"
        out = tmp_path / "hospitals_clean.csv"
        _write_csv(raw, [
            _make_row(ID="C001", LATITUDE="", LONGITUDE="-86.0"),
            _make_row(ID="C002"),
        ])
        prepare(raw_path=raw, out_path=out)
        rows = list(csv.DictReader(out.open(encoding="utf-8")))
        assert len(rows) == 1
        assert rows[0]["ID"] == "C002"

    def test_not_available_coord_dropped(self, tmp_path: Path) -> None:
        raw = tmp_path / "Hospitals.csv"
        out = tmp_path / "hospitals_clean.csv"
        _write_csv(raw, [
            _make_row(ID="C003", LATITUDE="NOT AVAILABLE"),
        ])
        prepare(raw_path=raw, out_path=out)
        rows = list(csv.DictReader(out.open(encoding="utf-8")))
        assert len(rows) == 0


class TestDuplicateIdDropped:
    def test_only_first_duplicate_kept(self, tmp_path: Path) -> None:
        raw = tmp_path / "Hospitals.csv"
        out = tmp_path / "hospitals_clean.csv"
        _write_csv(raw, [
            _make_row(ID="DUP001", NAME="First"),
            _make_row(ID="DUP001", NAME="Second"),
        ])
        stats = prepare(raw_path=raw, out_path=out)
        assert stats["dup_removed"] == 1
        rows = list(csv.DictReader(out.open(encoding="utf-8")))
        assert len(rows) == 1
        assert rows[0]["display_name"] == "First"


class TestCaseNormalisation:
    def test_name_upper_cased(self, tmp_path: Path) -> None:
        raw = tmp_path / "Hospitals.csv"
        out = tmp_path / "hospitals_clean.csv"
        _write_csv(raw, [_make_row(ID="N001", NAME="city general hospital")])
        prepare(raw_path=raw, out_path=out)
        rows = list(csv.DictReader(out.open(encoding="utf-8")))
        assert rows[0]["NAME"] == "CITY GENERAL HOSPITAL"

    def test_display_name_title_cased(self, tmp_path: Path) -> None:
        raw = tmp_path / "Hospitals.csv"
        out = tmp_path / "hospitals_clean.csv"
        _write_csv(raw, [_make_row(ID="N002", NAME="city general hospital")])
        prepare(raw_path=raw, out_path=out)
        rows = list(csv.DictReader(out.open(encoding="utf-8")))
        assert rows[0]["display_name"] == "City General Hospital"

    def test_city_upper_cased(self, tmp_path: Path) -> None:
        raw = tmp_path / "Hospitals.csv"
        out = tmp_path / "hospitals_clean.csv"
        _write_csv(raw, [_make_row(ID="N003", CITY="springfield")])
        prepare(raw_path=raw, out_path=out)
        rows = list(csv.DictReader(out.open(encoding="utf-8")))
        assert rows[0]["CITY"] == "SPRINGFIELD"


class TestStats:
    def test_report_counts_correct(self, tmp_path: Path) -> None:
        raw = tmp_path / "Hospitals.csv"
        out = tmp_path / "hospitals_clean.csv"
        _write_csv(raw, [
            _make_row(ID="S001", STATUS="OPEN"),
            _make_row(ID="S002", STATUS="CLOSED"),
            _make_row(ID="S003", STATUS="OPEN", LATITUDE=""),
        ])
        stats = prepare(raw_path=raw, out_path=out)
        assert stats["rows_in"] == 3
        assert stats["closed_removed"] == 1
        assert stats["rows_out"] == 1
