import csv
import io
from copy import deepcopy

import pytest

from powerschool_import import (
    DEFAULT_MAPPING_V1,
    ImportValidationError,
    NORMALIZER_REVISION,
    TRANSPORTATION_V2_CONTRACT,
    build_normalized_plan,
)


V2_HEADERS = [
    "student_number",
    "first_name",
    "last_name",
    "grade",
    "transport_status",
    "route_am",
    "route_pm",
    "school",
    "student_id",
]

OBSOLETE_V2_HEADERS = [
    "student_number",
    "first_name",
    "last_name",
    "grade",
    "transport_status",
    "route",
    "stop",
    "school",
    "student_id",
]

BRIGHTARROW_V2_HEADERS = [
    "BRIGHTARROW.003_student_number",
    "BRIGHTARROW.006_studentfname",
    "BRIGHTARROW.007_studentlname",
    "BRIGHTARROW.008_grade_level",
    "BRIGHTARROW.010_enroll_status",
    "BRIGHTARROW.013_bus_route",
    "BRIGHTARROW.014_bus_stop",
    "BRIGHTARROW.200_schoolid",
    "BRIGHTARROW.300_studentid",
]

V1_HEADERS = [
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

CONTACT_HEADERS = ["student_number", "contact_id", "first_name", "email"]


def _csv_bytes(headers, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


def _v2_row(student_number, route_am, route_pm="", *, student_id=None):
    return [
        student_number,
        "Ada",
        "Student",
        "5",
        "Active",
        route_am,
        route_pm,
        "205",
        student_id or f"SID-{student_number}",
    ]


def _contact_row(student_number, contact_id):
    return [student_number, contact_id, "Guardian", "guardian@example.test"]


def _plan(transport_headers, transport_rows, contact_rows, *, max_rows=5000,
          mapping=None):
    return build_normalized_plan(
        _csv_bytes(transport_headers, transport_rows),
        _csv_bytes(CONTACT_HEADERS, contact_rows),
        mapping or DEFAULT_MAPPING_V1,
        max_rows,
        50,
    )


def test_brightarrow_transportation_v2_raw_headers_expand_am_and_pm():
    result = _plan(
        BRIGHTARROW_V2_HEADERS,
        [_v2_row(
            "0001", "BUS 0042 AM", "BUS 0042 PM", student_id="300-1")],
        [_contact_row("0001", "C-1")],
    )

    student = result["students"][0]
    assert result["normalizer_revision"] == NORMALIZER_REVISION
    assert NORMALIZER_REVISION == "2026-08-27.4"
    assert result["preflight"]["transportation_contract"] == (
        TRANSPORTATION_V2_CONTRACT
    )
    assert TRANSPORTATION_V2_CONTRACT == (
        "students_combined_dual_route"
    )
    assert result["preflight"]["ok"] is True
    assert result["preflight"]["dual_route"] is True
    assert "am_delta_only" not in result["preflight"]
    assert "dual_route_delta_only" not in result["preflight"]
    assert result["metrics"]["transportation"]["period_am_rows"] == 1
    assert result["metrics"]["transportation"]["period_pm_rows"] == 1
    assert any(
        "Delta is the safe default" in warning
        and "Full Snapshot requires both AM and PM" in warning
        for warning in result["preflight"]["warnings"]
    )
    assert student["student_number"] == "0001"
    assert student["student_id"] == "300-1"
    assert student["grade"] == "5"
    assert student["transport_status"] == "Active"
    assert student["school"] == "205"
    assert student["stop"] == ""
    assert student["assignments"] == [
        {"route_prefix": "BUS", "route_number": "42", "period": "AM"},
        {"route_prefix": "BUS", "route_number": "42", "period": "PM"},
    ]


def test_v2_ignores_obsolete_route_requirement_in_persisted_mapping_profile():
    persisted_mapping = deepcopy(DEFAULT_MAPPING_V1)
    persisted_mapping["files"]["transportation"]["required"].append("route")

    result = _plan(
        V2_HEADERS,
        [_v2_row("0001", "BUS 42 AM", "BUS 42 PM")],
        [_contact_row("0001", "C-1")],
        mapping=persisted_mapping,
    )

    assert result["preflight"]["ok"] is True
    assert result["preflight"]["dual_route"] is True
    assert result["metrics"]["transportation"]["accepted_rows"] == 2


def test_v2_ignores_blank_and_known_non_bus_categories_without_row_issues():
    ignored_routes = [
        "",
        "Walker",
        "Walker - AM",
        "Door-to-Door",
        "Door to Door - Specialized",
        "Door – To – Door PM",
        "NTW",
        "NTW - Not Transported",
    ]
    rows = [_v2_row("0001", "BUS 42 AM", "BUS 42 PM")]
    rows.extend(
        _v2_row(f"I{index:03d}", route, route)
        for index, route in enumerate(ignored_routes, start=1)
    )

    result = _plan(
        V2_HEADERS,
        rows,
        [_contact_row("0001", "C-1")],
    )

    metrics = result["metrics"]["transportation"]
    assert result["preflight"]["ok"] is True
    assert metrics["accepted_rows"] == 2
    assert metrics["ignored_rows"] == len(ignored_routes) * 2
    assert metrics["ignored_blank_route_rows"] == 2
    assert metrics["ignored_non_bus_route_rows"] == (
        (len(ignored_routes) - 1) * 2
    )
    assert metrics["period_am_rows"] == 1
    assert metrics["period_pm_rows"] == 1
    assert metrics["rejected_rows"] == 0
    assert result["issues"] == []
    assert any(
        "known non-bus category" in warning
        for warning in result["preflight"]["warnings"]
    )


def test_v2_preflight_blocks_when_every_transport_row_is_ignored():
    result = _plan(
        V2_HEADERS,
        [
            _v2_row("0001", ""),
            _v2_row("0002", "Walker"),
            _v2_row("0003", "NTW - Not Transported"),
        ],
        [_contact_row("0001", "C-1")],
    )

    assert result["students"] == []
    assert result["issues"] == []
    assert result["preflight"]["ok"] is False
    assert result["preflight"]["valid_transport_rows"] == 0
    assert "route_am/route_pm" in result["preflight"]["errors"][0]
    assert "busnumber" not in result["preflight"]["errors"][0]
    assert result["metrics"]["transportation"]["ignored_rows"] == 6
    assert result["metrics"]["contacts"]["not_processed_rows"] == 1


def test_v2_ignores_opposite_suffix_leg_with_safe_warning():
    result = _plan(
        V2_HEADERS,
        [_v2_row("0001", "BUS 42 PM", "BUS 42 PM")],
        [_contact_row("0001", "C-1")],
    )

    metrics = result["metrics"]["transportation"]
    assert metrics["accepted_rows"] == 1
    assert metrics["period_am_rows"] == 0
    assert metrics["period_pm_rows"] == 1
    assert metrics["route_am_period_conflict_rows"] == 1
    assert metrics["route_pm_period_conflict_rows"] == 0
    assert metrics["warning_rows"] == 1
    assert result["students"][0]["assignments"] == [
        {"route_prefix": "BUS", "route_number": "42", "period": "PM"},
    ]
    assert [issue["classification"] for issue in result["issues"]] == [
        "warning"
    ]
    assert all(
        "BUS 42" not in error
        for error in result["issues"][0]["errors"]
    )
    assert "route leg was ignored" in result["issues"][0]["errors"][0]
    assert any(
        "contradictory suffix" in warning
        for warning in result["preflight"]["warnings"]
    )


def test_v2_invalid_direction_keeps_valid_opposite_assignment():
    result = _plan(
        V2_HEADERS,
        [_v2_row("0001", "not a route", "BUS 42 PM")],
        [_contact_row("0001", "C-1")],
    )

    metrics = result["metrics"]["transportation"]
    assert result["preflight"]["ok"] is True
    assert metrics["accepted_rows"] == 1
    assert metrics["invalid_route_am_rows"] == 1
    assert metrics["invalid_route_pm_rows"] == 0
    assert metrics["period_am_rows"] == 0
    assert metrics["period_pm_rows"] == 1
    assert metrics["rejected_rows"] == 0
    assert result["students"][0]["assignments"] == [{
        "route_prefix": "BUS", "route_number": "42", "period": "PM",
    }]
    assert result["issues"] == [{
        "file": "transportation",
        "row_number": 2,
        "classification": "warning",
        "errors": ["route_am is populated but cannot be normalized"],
    }]
    assert any(
        "valid opposite-period assignment was retained" in warning
        for warning in result["preflight"]["warnings"]
    )


def test_v2_preserves_different_am_and_pm_buses_without_student_conflict():
    result = _plan(
        V2_HEADERS,
        [_v2_row("0001", "BUS 42 AM", "BUS 43 PM")],
        [_contact_row("0001", "C-1")],
    )

    metrics = result["metrics"]["transportation"]
    student = result["students"][0]
    assert metrics["different_am_pm_route_rows"] == 1
    assert student["assignments"] == [
        {"route_prefix": "BUS", "route_number": "42", "period": "AM"},
        {"route_prefix": "BUS", "route_number": "43", "period": "PM"},
    ]
    assert "conflicts" not in student
    assert any(
        "different AM and PM buses" in warning
        for warning in result["preflight"]["warnings"]
    )


def test_obsolete_canonical_route_stop_contract_requires_template_941_reexport():
    with pytest.raises(ImportValidationError) as exc_info:
        _plan(
            OBSOLETE_V2_HEADERS,
            [[
                "0001", "Ada", "Student", "5", "Active",
                "BUS 42 AM", "BUS 42 PM", "205", "SID-1",
            ]],
            [_contact_row("0001", "C-1")],
        )

    message = str(exc_info.value)
    assert "template 941" in message
    assert "route_am" in message
    assert "route_pm" in message


def test_districtwide_contacts_outside_transport_scope_are_aggregated_only():
    outside_scope = [
        _contact_row(f"N{index:04d}", f"C-{index}")
        for index in range(1000)
    ]
    result = _plan(
        V2_HEADERS,
        [_v2_row("0001", "BUS 42 AM")],
        [_contact_row("0001", "C-IN")] + outside_scope,
    )

    metrics = result["metrics"]["contacts"]
    assert metrics["accepted_rows"] == 1
    assert metrics["ignored_rows"] == 1000
    assert metrics["ignored_no_transport_rows"] == 1000
    assert metrics["rejected_rows"] == 0
    assert result["metrics"]["contact_sources"]["contacts"][
        "ignored_no_transport_rows"
    ] == 1000
    assert result["issues"] == []
    assert len(result["students"][0]["contacts"]) == 1
    assert any(
        "outside the valid Transportation scope" in warning
        for warning in result["preflight"]["warnings"]
    )


def test_invalid_contact_student_numbers_remain_rejected():
    result = _plan(
        V2_HEADERS,
        [_v2_row("0001", "BUS 42 AM")],
        [
            _contact_row("", "C-MISSING"),
            _contact_row("bad student", "C-BAD"),
            _contact_row("OUTSIDE", "C-OUT"),
        ],
    )

    metrics = result["metrics"]["contacts"]
    assert metrics["rejected_rows"] == 2
    assert metrics["ignored_no_transport_rows"] == 1
    assert len(result["issues"]) == 2
    assert all(issue["classification"] == "rejected" for issue in result["issues"])


def test_transportation_v1_headers_remain_supported():
    result = _plan(
        V1_HEADERS,
        [[
            "0001", "SID-1", "Ada", "Student", "205", "5",
            "BUS42", "Stop 10", "AM", "true",
        ]],
        [_contact_row("0001", "C-1")],
    )

    assert result["preflight"]["ok"] is True
    assert result["metrics"]["transportation"]["accepted_rows"] == 1
    assert result["metrics"]["transportation"]["ignored_rows"] == 0
    assert result["students"][0]["assignments"] == [{
        "route_prefix": "BUS",
        "route_number": "42",
        "period": "AM",
    }]
