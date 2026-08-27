import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

TRANSPORTATION_HEADERS = [
    "TRANSPORTATION.student_number",
    "TRANSPORTATION.student_dcid",
    "TRANSPORTATION.studentfname",
    "TRANSPORTATION.studentlname",
    "TRANSPORTATION.schoolid",
    "TRANSPORTATION.grade_level",
    "TRANSPORTATION.busnumber",
    "TRANSPORTATION.stopnumber",
    "TRANSPORTATION.fromto",
    "TRANSPORTATION.ride_on_enabledToday",
]

CONTACT_HEADERS = [
    "BRIGHTARROW.003_student_number",
    "BRIGHTARROW.600_00_contact_id",
    "BRIGHTARROW.600_04_contact_std_detailid",
    "BRIGHTARROW.600_01_contact_firstname",
    "BRIGHTARROW.600_02_contact_lastname",
    "BRIGHTARROW.600_03_contact_relationship",
    "BRIGHTARROW.601_01_home_phone",
    "BRIGHTARROW.602_01_phone2",
    "BRIGHTARROW.603_01_phone3",
    "BRIGHTARROW.604_01_phone4",
    "BRIGHTARROW.605_01_phone5",
    "BRIGHTARROW.606_01_phone6",
    "BRIGHTARROW.607_01_phone7",
    "BRIGHTARROW.608_01_phone8",
    "BRIGHTARROW.609_01_phone9",
    "BRIGHTARROW.801_email1",
    "BRIGHTARROW.802_email2",
    "BRIGHTARROW.803_email3",
]


def _headers(filename):
    with (ROOT / "static" / "templates" / filename).open(
            encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    assert len(rows) == 1, "downloadable contracts must remain header-only"
    return rows[0]


def test_saved_powerschool_template_contracts_match_verified_exports():
    assert _headers("powerschool-transportation-v1.csv") == TRANSPORTATION_HEADERS
    assert _headers("powerschool-student-contacts-v1.csv") == CONTACT_HEADERS
    assert _headers("powerschool-guardian-contacts-v1.csv") == CONTACT_HEADERS


def test_import_ui_exposes_duplicate_recovery_and_preflight_without_unsafe_dom():
    source = (ROOT / "templates" / "admin" / "powerschool_import.html").read_text(
        encoding="utf-8")
    assert "Open existing analysis" in source
    assert "Re-analyze against current state" in source
    assert "force_reanalyze" in source
    assert "existing_batch_id" in source
    assert "renderPreflight" in source
    assert "reviewPageSize = 250" in source
    assert "review-prev-page" in source
    assert "review-next-page" in source
    assert "byId('confirm-deactivations').checked = false" in source
    assert "no_valid_transportation_rows" not in source  # backend owns validation codes
    assert "innerHTML" not in source
    assert "eval(" not in source


def test_operator_guidance_covers_source_sentinels_and_safe_delta_policy():
    guide = (ROOT / "templates" / "admin" / "powerschool_guide.html").read_text(
        encoding="utf-8")
    runbook = (ROOT / "docs" / "powerschool-import-runbook.md").read_text(
        encoding="utf-8")
    for source in (guide, runbook):
        assert "sentinel" in source
        assert "0" in source
        assert "Transportation" in source
        assert "Delta" in source
        assert "Open existing analysis" in source
        assert "Re-analyze against current state" in source
