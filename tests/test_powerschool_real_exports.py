import csv
import io
import json

from powerschool_import import (
    DEFAULT_MAPPING_V1,
    NORMALIZER_REVISION,
    STUDENT_SELF_CONTACT_ID,
    build_normalized_plan,
)


TRANSPORT_HEADERS = [
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


def _csv_bytes(headers, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _transport_row(student="0001", route="TEST1"):
    return [
        student, f"SID-{student}", "Ada", "Student", "205", "5",
        route, "10", "AM", "true",
    ]


def _contact_row(student="0001", contact_id="C-1", detail_id="D-1",
                 first="Grace", last="Guardian", relationship="Mother",
                 phone="708-555-0101", email1="grace@example.test",
                 email2="", email3=""):
    return [
        student, contact_id, detail_id, first, last, relationship, phone,
        "", "", "", "", "", "", "", "", email1, email2, email3,
    ]


def _split_plan(transport_rows, student_rows, guardian_rows):
    return build_normalized_plan(
        _csv_bytes(TRANSPORT_HEADERS, transport_rows),
        None,
        DEFAULT_MAPPING_V1,
        100,
        50,
        contact_sources=[
            {
                "key": "student_contacts",
                "payload": _csv_bytes(CONTACT_HEADERS, student_rows),
                "force_relationship": "student",
            },
            {
                "key": "guardian_contacts",
                "payload": _csv_bytes(CONTACT_HEADERS, guardian_rows),
                "default_relationship": "guardian",
            },
        ],
    )


def test_real_split_exports_use_stable_student_self_identity_and_ignore_overlap():
    student_self = _contact_row(
        contact_id="0", detail_id="0", first="Ada", last="Student",
        relationship="", phone="708-555-0100", email1="ada@example.test",
    )
    guardian = _contact_row(contact_id="G-1", detail_id="GD-1")
    placeholder = _contact_row(
        contact_id="", detail_id="", first="", last="", relationship="",
        phone="", email1="",
    )

    result = _split_plan(
        [_transport_row()],
        [student_self],
        [student_self, guardian, placeholder],
    )

    contacts = {
        contact["contact_id"]: contact
        for contact in result["students"][0]["contacts"]
    }
    assert set(contacts) == {STUDENT_SELF_CONTACT_ID, "G-1"}
    assert contacts[STUDENT_SELF_CONTACT_ID]["relationship"] == "student"
    assert STUDENT_SELF_CONTACT_ID == "student-self"
    assert all(
        value not in STUDENT_SELF_CONTACT_ID
        for value in ("Ada", "ada@example.test", "7085550100", "0001")
    )
    assert result["metrics"]["contacts"]["accepted_rows"] == 2
    assert result["metrics"]["contacts"]["ignored_rows"] == 2
    assert result["metrics"]["contacts"]["student_self_identity_rows"] == 1
    assert (
        result["metrics"]["contacts"][
            "ignored_guardian_student_overlap_rows"
        ]
        == 1
    )
    assert result["metrics"]["contacts"]["ignored_placeholder_rows"] == 1
    # High-volume source artifacts remain visible in aggregate metrics without
    # creating thousands of review rows or DOM nodes.
    assert result["issues"] == []


def test_guardian_zero_sentinel_with_relationship_is_ignored_and_warned():
    anomalous_guardian = _contact_row(
        contact_id="0", detail_id="0", relationship="Mother")

    result = _split_plan(
        [_transport_row()],
        [],
        [anomalous_guardian],
    )

    assert result["students"][0]["contacts"] == []
    assert result["metrics"]["contacts"]["ignored_rows"] == 1
    assert (
        result["metrics"]["contacts"]["ignored_guardian_zero_anomaly_rows"]
        == 1
    )
    assert result["metrics"]["contacts"]["warning_rows"] == 1
    assert [issue["classification"] for issue in result["issues"]] == [
        "warning"
    ]


def test_nonzero_contact_detail_id_wins_over_zero_primary_sentinel():
    guardian = _contact_row(
        contact_id="0", detail_id="G-DETAIL-1", relationship="Mother")

    result = _split_plan([_transport_row()], [], [guardian])

    assert result["students"][0]["contacts"][0]["contact_id"] == "G-DETAIL-1"
    assert result["metrics"]["contacts"]["accepted_rows"] == 1
    assert result["metrics"]["contacts"]["ignored_rows"] == 0


def test_partial_invalid_email_is_omitted_without_rejecting_valid_contact_data():
    result = build_normalized_plan(
        _csv_bytes(TRANSPORT_HEADERS, [_transport_row()]),
        _csv_bytes(CONTACT_HEADERS, [
            _contact_row(email1="valid@example.test", email2="not-an-email")
        ]),
        DEFAULT_MAPPING_V1,
        100,
        50,
    )

    contact = result["students"][0]["contacts"][0]
    assert contact["email"] == "valid@example.test"
    assert "warnings" not in contact
    assert result["metrics"]["contacts"]["accepted_rows"] == 1
    assert result["metrics"]["contacts"]["rejected_rows"] == 0
    assert result["metrics"]["contacts"]["invalid_email_rows"] == 1
    assert result["metrics"]["contacts"]["invalid_email_values"] == 1
    assert any(issue["classification"] == "warning" for issue in result["issues"])


def test_omitted_invalid_email_does_not_create_a_false_contact_conflict():
    result = build_normalized_plan(
        _csv_bytes(TRANSPORT_HEADERS, [_transport_row()]),
        _csv_bytes(CONTACT_HEADERS, [
            _contact_row(email1="valid@example.test", email2="not-an-email"),
            _contact_row(email1="valid@example.test"),
        ]),
        DEFAULT_MAPPING_V1,
        100,
        50,
    )

    assert len(result["students"][0]["contacts"]) == 1
    assert result["metrics"]["contacts"]["duplicate_rows"] == 1
    assert result["metrics"]["contacts"]["conflict_rows"] == 0
    assert not result["students"][0].get("conflicts")


def test_zero_valid_transport_rows_return_blocking_preflight_without_contact_noise():
    result = build_normalized_plan(
        _csv_bytes(TRANSPORT_HEADERS, [_transport_row(route="")]),
        _csv_bytes(CONTACT_HEADERS, [_contact_row()]),
        DEFAULT_MAPPING_V1,
        100,
        50,
    )

    assert result["students"] == []
    assert result["preflight"]["ok"] is False
    assert result["preflight"]["transport_rows"] == 1
    assert result["preflight"]["valid_transport_rows"] == 0
    assert result["preflight"]["valid_students"] == 0
    assert result["preflight"]["errors"]
    assert result["metrics"]["contacts"]["not_processed_rows"] == 1
    assert result["metrics"]["contacts"]["rejected_rows"] == 0
    assert len(result["issues"]) == 1
    assert result["issues"][0]["file"] == "transportation"


def test_metrics_and_preflight_are_pii_safe_and_revisioned():
    result = _split_plan(
        [_transport_row()],
        [_contact_row(
            contact_id="0", detail_id="0", first="PrivateFirst",
            last="PrivateLast", relationship="", phone="708-555-9999",
            email1="private@example.test",
        )],
        [],
    )

    diagnostic_payload = json.dumps({
        "metrics": result["metrics"],
        "preflight": result["preflight"],
    })
    assert result["normalizer_revision"] == NORMALIZER_REVISION
    assert result["preflight"]["ok"] is True
    assert result["preflight"]["valid_transport_rows"] == 1
    for pii in (
        "PrivateFirst", "PrivateLast", "private@example.test",
        "708-555-9999", "0001",
    ):
        assert pii not in diagnostic_payload


def test_combined_v1_contact_id_zero_contract_is_unchanged():
    result = build_normalized_plan(
        _csv_bytes(TRANSPORT_HEADERS, [_transport_row()]),
        _csv_bytes(CONTACT_HEADERS, [
            _contact_row(contact_id="0", detail_id="0")
        ]),
        DEFAULT_MAPPING_V1,
        100,
        50,
    )

    assert result["students"][0]["contacts"][0]["contact_id"] == "0"
