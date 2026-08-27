"""Pure parsing and normalization for the versioned PowerSchool import flow.

This module deliberately has no Flask or database dependencies.  Uploaded column
names are resolved through the selected mapping profile; row positions are never
part of the contract.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
from collections import defaultdict


EMAIL_RE = re.compile(r"^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
NORMALIZER_REVISION = "2026-08-27.6"
STUDENT_SELF_CONTACT_ID = "student-self"
TRANSPORTATION_V2_CONTRACT = "students_combined_dual_route"
ROUTE_RE = re.compile(
    r"^([A-Z]+(?:\s+[A-Z]+)*)\s*0*([0-9]+)\s*(AM|MD|PM|A|P)?$",
    re.IGNORECASE,
)


DEFAULT_MAPPING_V1 = {
    "identity": "student_number",
    "files": {
        "transportation": {
            "required": ["student_number"],
            "columns": {
                "student_number": [
                    "student_number", "STUDENTS.Student_Number",
                    "BRIGHTARROW.003_student_number",
                    "TRANSPORTATION.student_number",
                ],
                "student_id": [
                    "student_id", "STUDENTS.ID", "STUDENTS.dcid",
                    "student_dcid", "TRANSPORTATION.StudentID",
                    "TRANSPORTATION.student_dcid",
                    "BRIGHTARROW.300_studentid",
                ],
                "household_id": [
                    "household_id", "family_id", "source_identifier",
                    "STUDENTS.Family_Ident",
                ],
                "first_name": [
                    "first_name", "studentfname", "STUDENTS.First_Name",
                    "BRIGHTARROW.006_studentfname",
                    "TRANSPORTATION.studentfname",
                ],
                "last_name": [
                    "last_name", "studentlname", "STUDENTS.Last_Name",
                    "BRIGHTARROW.007_studentlname",
                    "TRANSPORTATION.studentlname",
                ],
                "school": [
                    "school", "school_id", "schoolid", "STUDENTS.SchoolID",
                    "BRIGHTARROW.200_schoolid", "TRANSPORTATION.SchoolID",
                    "TRANSPORTATION.schoolid",
                ],
                "grade": [
                    "grade", "grade_level", "STUDENTS.Grade_Level",
                    "TRANSPORTATION.grade_level",
                    "BRIGHTARROW.008_grade_level",
                ],
                "route": [
                    "route", "bus_route", "busnumber", "STUDENTS.Bus_Route",
                    "routenumber", "TRANSPORTATION.RouteNumber",
                    "TRANSPORTATION.routenumber",
                    "TRANSPORTATION.BusNumber",
                    "TRANSPORTATION.busnumber",
                ],
                "route_am": [
                    "route_am", "BRIGHTARROW.013_bus_route",
                ],
                "route_pm": [
                    "route_pm", "BRIGHTARROW.014_bus_stop",
                ],
                "stop": [
                    "stop", "bus_stop", "stopnumber",
                    "TRANSPORTATION.StopNumber",
                    "TRANSPORTATION.stopnumber",
                ],
                "period": [
                    "period", "direction", "fromto", "TRANSPORTATION.FromTo",
                    "TRANSPORTATION.fromto", "TRANSPORTATION.Type",
                    "TRANSPORTATION.type",
                ],
                "transport_status": [
                    "transport_status", "active", "ride_on_enabledToday",
                    "TRANSPORTATION.ride_on_enabledToday",
                    "BRIGHTARROW.010_enroll_status",
                ],
                "school_year": ["school_year", "year"],
                "source_id": [
                    "source_id", "TRANSPORTATION.ID", "TRANSPORTATION.dcid",
                ],
            },
        },
        "contacts": {
            "required": ["student_number", "contact_id"],
            "columns": {
                "student_number": [
                    "student_number", "BRIGHTARROW.003_student_number",
                    "STUDENTS.Student_Number",
                ],
                "contact_id": [
                    "contact_id", "contact_dcid", "600_00_contact_id",
                    "600_04_contact_std_detailid",
                    "BRIGHTARROW.600_00_contact_id",
                    "BRIGHTARROW.600_04_contact_std_detailid",
                ],
                "first_name": [
                    "first_name", "contact_first_name", "600_01_contact_firstname",
                    "BRIGHTARROW.600_01_contact_firstname",
                ],
                "last_name": [
                    "last_name", "contact_last_name", "600_02_contact_lastname",
                    "BRIGHTARROW.600_02_contact_lastname",
                ],
                "relationship": [
                    "relationship", "role", "600_03_contact_relationship",
                    "BRIGHTARROW.600_03_contact_relationship",
                ],
                "email": [
                    "email", "801_email1", "802_email2", "803_email3",
                    "BRIGHTARROW.801_email1",
                    "BRIGHTARROW.802_email2", "BRIGHTARROW.803_email3",
                ],
                "phone": [
                    "phone", "home_phone", "601_01_home_phone", "602_01_phone2",
                    "603_01_phone3", "604_01_phone4", "605_01_phone5",
                    "606_01_phone6", "607_01_phone7", "608_01_phone8",
                    "609_01_phone9", "BRIGHTARROW.601_01_home_phone",
                    "BRIGHTARROW.602_01_phone2", "BRIGHTARROW.603_01_phone3",
                    "BRIGHTARROW.604_01_phone4", "BRIGHTARROW.605_01_phone5",
                    "BRIGHTARROW.606_01_phone6", "BRIGHTARROW.607_01_phone7",
                    "BRIGHTARROW.608_01_phone8", "BRIGHTARROW.609_01_phone9",
                ],
                "notification_preference": [
                    "notification_preference", "notify", "preferred_contact_method",
                ],
                "priority": ["priority", "contact_priority", "custody_priority"],
            },
        },
    },
    "period_aliases": {
        "AM": ["AM", "A", "ARRIVAL", "TO SCHOOL", "INBOUND"],
        "MD": ["MD", "MIDDAY", "NOON"],
        "PM": ["PM", "P", "DEPARTURE", "FROM SCHOOL", "OUTBOUND"],
    },
}


class ImportValidationError(ValueError):
    """A safe, operator-facing input validation error."""


def normalize_text(value, maximum=None):
    result = unicodedata.normalize("NFKC", str(value or "")).replace("\u00a0", " ")
    result = re.sub(r"\s+", " ", result).strip()
    return result[:maximum] if maximum else result


def normalize_identifier(value, maximum=160):
    result = normalize_text(value, maximum)
    if result.endswith(".0") and result[:-2].isdigit():
        result = result[:-2]
    return result


def normalize_email_values(values):
    valid, invalid = set(), set()
    for value in values:
        for token in re.split(r"[,;]", normalize_text(value, 1000)):
            email = token.strip().lower()
            if not email:
                continue
            (valid if EMAIL_RE.fullmatch(email) else invalid).add(email)
    return ",".join(sorted(valid)), sorted(invalid)


def normalize_phone_values(values):
    for value in values:
        digits = re.sub(r"\D", "", normalize_text(value, 80))
        if len(digits) == 10:
            return "+1" + digits
        if len(digits) == 11 and digits.startswith("1"):
            return "+" + digits
    return ""


def _header_lookup(headers):
    return {normalize_text(header).casefold(): header for header in headers}


def _matching_headers(headers, aliases):
    lookup = _header_lookup(headers)
    matches = []
    for alias in aliases:
        key = normalize_text(alias).casefold()
        if key in lookup:
            matches.append(lookup[key])
            continue
        suffix = "." + key
        for normalized, original in lookup.items():
            if normalized.endswith(suffix) and original not in matches:
                matches.append(original)
    return matches


def resolve_mapping(headers, file_mapping):
    columns = file_mapping.get("columns") or {}
    resolved = {
        canonical: _matching_headers(headers, aliases)
        for canonical, aliases in columns.items()
    }
    missing = [name for name in file_mapping.get("required", []) if not resolved.get(name)]
    if missing:
        raise ImportValidationError(
            "Missing required mapped column(s): " + ", ".join(sorted(missing))
        )
    return resolved


def _transportation_contract(headers):
    """Identify the district's Students Combined fallback by its field contract.

    The v2 saved template deliberately renames the BrightArrow source columns,
    but diagnostic exports may retain the original ``BRIGHTARROW.*`` labels.
    Both signatures omit a separate period column and therefore cannot prove a
    complete district-wide AM/PM snapshot when both directional fields exist.
    """
    keys = {normalize_text(header).casefold() for header in headers}
    old_canonical = {
        "student_number", "student_id", "transport_status", "route", "stop",
    }
    canonical = {"student_number", "route_am", "route_pm"}
    brightarrow = {
        "brightarrow.003_student_number",
        "brightarrow.013_bus_route",
        "brightarrow.014_bus_stop",
    }
    has_explicit_period = bool({"period", "direction", "fromto"} & keys)
    if old_canonical.issubset(keys) and not has_explicit_period:
        raise ImportValidationError(
            "This is the obsolete single-route Transportation v2 contract. "
            "Re-export PowerSchool template 941 with canonical route_am and "
            "route_pm columns."
        )
    if canonical.issubset(keys) or brightarrow.issubset(keys):
        return TRANSPORTATION_V2_CONTRACT
    return "legacy"


def _values(row, resolved, canonical):
    return [row.get(header, "") for header in resolved.get(canonical, [])]


def _first(row, resolved, canonical):
    for value in _values(row, resolved, canonical):
        cleaned = normalize_text(value)
        if cleaned:
            return cleaned
    return ""


def read_csv_payload(payload, max_rows, max_columns):
    if not payload:
        raise ImportValidationError("The uploaded CSV file is empty.")
    try:
        content = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportValidationError("CSV files must use UTF-8 encoding.") from exc
    reader = csv.DictReader(io.StringIO(content, newline=""))
    headers = [normalize_text(item) for item in (reader.fieldnames or [])]
    if not headers or any(not item for item in headers):
        raise ImportValidationError("The CSV header is missing or contains a blank column.")
    if len(headers) > max_columns:
        raise ImportValidationError(f"The CSV exceeds the {max_columns}-column limit.")
    if len(set(item.casefold() for item in headers)) != len(headers):
        raise ImportValidationError("The CSV contains duplicate column names.")
    rows = []
    for number, row in enumerate(reader, start=2):
        if len(rows) >= max_rows:
            raise ImportValidationError(f"The CSV exceeds the {max_rows}-row limit.")
        if None in row:
            raise ImportValidationError(f"Row {number} contains more values than the header.")
        rows.append((number, row))
    return headers, rows


def _period_alias_map(mapping):
    result = {}
    for canonical, aliases in (mapping.get("period_aliases") or {}).items():
        for alias in aliases:
            result[normalize_text(alias).upper()] = canonical.upper()
    return result


def normalize_route(value):
    raw = normalize_text(value, 100).upper()
    raw = re.sub(r"(?<=[A-Z])(?=\d)", " ", raw)
    raw = re.sub(r"(?<=\d)(?=[AP](?:M)?$)", " ", raw)
    raw = normalize_text(raw)
    match = ROUTE_RE.fullmatch(raw)
    if not match:
        return None
    prefix, number, suffix = match.groups()
    suffix = (suffix or "").upper()
    if suffix == "A":
        suffix = "AM"
    elif suffix == "P":
        suffix = "PM"
    return {
        "prefix": normalize_text(prefix).upper(),
        "number": str(int(number)),
        "period": suffix or None,
    }


def _ignored_transport_route_reason(value):
    """Classify rows that explicitly do not represent a bus assignment.

    BrightArrow Students Combined is district-wide and emits blank routes plus
    a small set of categorical non-bus values. These rows are expected source
    scope, not malformed assignments, so they belong in aggregate metrics and
    not in per-row review issues. Unknown non-empty values remain rejectable.
    """
    raw = normalize_text(value, 100).upper()
    if not raw:
        return "blank"

    normalized = raw.replace("\u2013", "-").replace("\u2014", "-")
    if re.fullmatch(r"WALKER(?:\b.*)?", normalized):
        return "known_non_bus"
    if re.fullmatch(
            r"DOOR\s*[- ]*\s*TO\s*[- ]*\s*DOOR(?:\b.*)?", normalized):
        return "known_non_bus"
    if re.fullmatch(r"NTW(?:\s*-\s*.*)?", normalized):
        return "known_non_bus"
    if re.fullmatch(r"0", normalized):
        return "known_non_bus"
    if re.fullmatch(r"WALKER\s*WALKER", normalized):
        return "known_non_bus"
    if re.fullmatch(r"DOOR\s*-\s*T0\s*-\s*DOOR", normalized):
        return "known_non_bus"
    return None


def _normalize_period(value, aliases):
    raw = normalize_text(value).upper()
    return aliases.get(raw)


def _new_row_metrics(input_rows):
    return {
        "input_rows": input_rows,
        "accepted_rows": 0,
        "duplicate_rows": 0,
        "conflict_rows": 0,
        "rejected_rows": 0,
        "ignored_rows": 0,
        "warning_rows": 0,
    }


def _contact_has_payload(row, resolved):
    """Return whether a source row contains anything beyond its student key.

    BrightArrow emits one empty guardian placeholder for some students. Those
    rows are not contacts and must not be converted into identities. The check
    deliberately considers only mapped fields and never derives an identity
    from names, email addresses, or phone numbers.
    """
    return any(
        normalize_text(value)
        for canonical in (
            "first_name", "last_name", "relationship", "email", "phone",
            "notification_preference", "priority",
        )
        for value in _values(row, resolved, canonical)
    )


def build_normalized_plan(transport_payload, contacts_payload, mapping, max_rows,
                          max_columns, contact_sources=None):
    files = mapping.get("files") or {}
    if "transportation" not in files or "contacts" not in files:
        raise ImportValidationError("The selected mapping profile is incomplete.")

    transport_headers, transport_rows = read_csv_payload(
        transport_payload, max_rows, max_columns)
    transport_contract = _transportation_contract(transport_headers)
    is_transportation_v2 = transport_contract == TRANSPORTATION_V2_CONTRACT
    transport_file_mapping = dict(files["transportation"])
    # Profiles are upgraded additively in production, so an older persisted
    # profile can still list the obsolete canonical ``route`` as required.
    # Route requirements are contract-specific and enforced immediately below.
    transport_file_mapping["required"] = [
        name for name in transport_file_mapping.get("required", [])
        if name not in {"route", "route_am", "route_pm"}
    ]
    transport_map = resolve_mapping(transport_headers, transport_file_mapping)
    required_route_columns = (
        ("route_am", "route_pm") if is_transportation_v2 else ("route",)
    )
    missing_route_columns = [
        name for name in required_route_columns if not transport_map.get(name)
    ]
    if missing_route_columns:
        raise ImportValidationError(
            "Missing required mapped column(s): "
            + ", ".join(sorted(missing_route_columns))
        )
    if contact_sources is None:
        if contacts_payload is None:
            raise ImportValidationError("A contacts export is required.")
        contact_sources = [{
            "key": "contacts", "payload": contacts_payload,
            "force_relationship": None, "default_relationship": None,
        }]
        split_contacts = False
    else:
        if contacts_payload is not None:
            raise ImportValidationError(
                "Use either one combined contacts export or the two PowerSchool contact exports.")
        split_contacts = True
        if not isinstance(contact_sources, (list, tuple)) or not contact_sources:
            raise ImportValidationError("Both PowerSchool contact exports are required.")

    parsed_contact_sources = []
    total_contact_rows = 0
    used_source_keys = set()
    for source in contact_sources:
        source_key = normalize_text(source.get("key"), 40)
        payload = source.get("payload")
        if not source_key or source_key in used_source_keys or not isinstance(payload, bytes):
            raise ImportValidationError("The contacts export configuration is invalid.")
        used_source_keys.add(source_key)
        contact_headers, contact_rows = read_csv_payload(
            payload, max_rows, max_columns)
        total_contact_rows += len(contact_rows)
        if total_contact_rows > max_rows:
            raise ImportValidationError(
                f"The contact exports contain more than {max_rows} total data rows.")
        parsed_contact_sources.append({
            "key": source_key,
            "payload": payload,
            "headers": contact_headers,
            "rows": contact_rows,
            "mapping": resolve_mapping(contact_headers, files["contacts"]),
            "force_relationship": normalize_text(
                source.get("force_relationship"), 40).lower(),
            "default_relationship": normalize_text(
                source.get("default_relationship"), 40).lower(),
        })
    aliases = _period_alias_map(mapping)

    students = {}
    row_issues = []
    seen_transport = set()
    transportation_metrics = _new_row_metrics(len(transport_rows))
    transportation_metrics.update({
        "ignored_blank_route_rows": 0,
        "ignored_non_bus_route_rows": 0,
        "ignored_missing_period_rows": 0,
        "period_am_rows": 0,
        "period_md_rows": 0,
        "period_pm_rows": 0,
        "route_am_period_conflict_rows": 0,
        "route_pm_period_conflict_rows": 0,
        "invalid_route_am_rows": 0,
        "invalid_route_pm_rows": 0,
        "different_am_pm_route_rows": 0,
        "dual_route": is_transportation_v2,
        "contract": transport_contract,
    })

    def proposal_for(row, student_number):
        proposal = students.setdefault(student_number, {
            "student_number": student_number,
            "student_id": normalize_identifier(_first(row, transport_map, "student_id")),
            "household_id": normalize_identifier(
                _first(row, transport_map, "household_id") or student_number),
            "first_name": normalize_text(_first(row, transport_map, "first_name"), 80),
            "last_name": normalize_text(_first(row, transport_map, "last_name"), 80),
            "school": normalize_text(_first(row, transport_map, "school"), 100),
            "grade": normalize_text(_first(row, transport_map, "grade"), 30),
            "stop": normalize_text(_first(row, transport_map, "stop"), 160),
            "transport_status": normalize_text(
                _first(row, transport_map, "transport_status"), 40),
            "school_year": normalize_text(_first(row, transport_map, "school_year"), 20),
            "assignments": [], "contacts": [], "source_rows": [],
        })
        for field in ("first_name", "last_name", "school", "grade", "stop"):
            incoming = normalize_text(_first(row, transport_map, field))
            if incoming and proposal.get(field) and proposal[field] != incoming:
                proposal.setdefault("conflicts", []).append(
                    f"transportation rows disagree on {field}")
            elif incoming:
                proposal[field] = incoming
        return proposal

    def accept_assignment(row_number, row, student_number, route, period):
        route_key = f"{route['prefix']}|{route['number']}"
        duplicate_key = (student_number, route_key, period or "ALL")
        if duplicate_key in seen_transport:
            transportation_metrics["duplicate_rows"] += 1
            row_issues.append({
                "file": "transportation", "row_number": row_number,
                "classification": "duplicate",
                "errors": ["duplicate transportation assignment"],
            })
            return False
        seen_transport.add(duplicate_key)
        transportation_metrics["accepted_rows"] += 1
        if period in {"AM", "MD", "PM"}:
            transportation_metrics[f"period_{period.lower()}_rows"] += 1
        proposal = proposal_for(row, student_number)
        proposal["assignments"].append({
            "route_prefix": route["prefix"], "route_number": route["number"],
            "period": period or "ALL",
        })
        if row_number not in proposal["source_rows"]:
            proposal["source_rows"].append(row_number)
        return True

    for row_number, row in transport_rows:
        student_number = normalize_identifier(
            _first(row, transport_map, "student_number"))
        student_errors = []
        if not student_number:
            student_errors.append("student_number is required")
        elif not IDENTIFIER_RE.fullmatch(student_number):
            student_errors.append(
                "student_number contains unsupported characters")

        if is_transportation_v2:
            directional_routes = {}
            safe_warnings = []
            for period, field_name in (("AM", "route_am"), ("PM", "route_pm")):
                route_raw = _first(row, transport_map, field_name)
                ignored_route_reason = _ignored_transport_route_reason(route_raw)
                if ignored_route_reason:
                    transportation_metrics["ignored_rows"] += 1
                    metric_key = (
                        "ignored_blank_route_rows"
                        if ignored_route_reason == "blank"
                        else "ignored_non_bus_route_rows"
                    )
                    transportation_metrics[metric_key] += 1
                    continue

                route = normalize_route(route_raw)
                if not route:
                    transportation_metrics[f"invalid_{field_name}_rows"] += 1
                    safe_warnings.append(
                        f"{field_name} is populated but cannot be normalized")
                    continue

                explicit_period = route.get("period")
                if explicit_period and explicit_period != period:
                    transportation_metrics[
                        f"{field_name}_period_conflict_rows"] += 1
                    safe_warnings.append(
                        f"{field_name} suffix conflicts with {period} column "
                        "semantics; the contradictory route leg was ignored")
                    continue
                directional_routes[period] = route

            if not directional_routes and not safe_warnings:
                continue

            if student_errors:
                transportation_metrics["rejected_rows"] += 1
                row_issues.append({
                    "file": "transportation", "row_number": row_number,
                    "classification": "rejected", "errors": student_errors,
                })
                continue

            if {"AM", "PM"}.issubset(directional_routes):
                am_route = directional_routes["AM"]
                pm_route = directional_routes["PM"]
                if ((am_route["prefix"], am_route["number"])
                        != (pm_route["prefix"], pm_route["number"])):
                    transportation_metrics["different_am_pm_route_rows"] += 1

            for period in ("AM", "PM"):
                route = directional_routes.get(period)
                if route:
                    accept_assignment(
                        row_number, row, student_number, route, period)

            if safe_warnings:
                transportation_metrics["warning_rows"] += 1
                row_issues.append({
                    "file": "transportation", "row_number": row_number,
                    "classification": "warning", "errors": safe_warnings,
                })
            continue

        route_raw = _first(row, transport_map, "route")
        ignored_route_reason = _ignored_transport_route_reason(route_raw)
        if ignored_route_reason:
            transportation_metrics["ignored_rows"] += 1
            metric_key = (
                "ignored_blank_route_rows"
                if ignored_route_reason == "blank"
                else "ignored_non_bus_route_rows"
            )
            transportation_metrics[metric_key] += 1
            continue

        route = normalize_route(route_raw)
        period_raw = _first(row, transport_map, "period")
        period = _normalize_period(period_raw, aliases) if period_raw else None
        if route and not period:
            period = route.get("period")
        errors = list(student_errors)
        if not route:
            errors.append("route is blank or cannot be normalized")
        if period_raw and not period:
            errors.append("period is not a configured AM/MD/PM alias")
        if errors:
            transportation_metrics["rejected_rows"] += 1
            row_issues.append({
                "file": "transportation", "row_number": row_number,
                "classification": "rejected", "errors": errors,
            })
            continue
        accept_assignment(
            row_number, row, student_number, route, period)

    transportation_metrics.update({
        "valid_assignments": len(seen_transport),
        "valid_students": len(students),
    })
    valid_transport_rows = transportation_metrics["accepted_rows"]
    preflight = {
        "ok": bool(valid_transport_rows),
        "errors": [] if valid_transport_rows else [
            "No valid bus assignments were found. Verify that the PowerSchool "
            "Transportation export contains populated student_number and "
            f"{'route_am/route_pm' if is_transportation_v2 else 'route/busnumber'} "
            "values before processing contacts."
        ],
        "warnings": [],
        "transport_rows": len(transport_rows),
        "valid_transport_rows": valid_transport_rows,
        "valid_students": len(students),
        "transportation_contract": transport_contract,
        "dual_route": is_transportation_v2,
        "transportation": dict(transportation_metrics),
    }

    seen_contacts = {}
    contact_metrics = _new_row_metrics(total_contact_rows)
    contact_metrics.update({
        "invalid_email_rows": 0,
        "invalid_email_values": 0,
        "ignored_placeholder_rows": 0,
        "ignored_guardian_student_overlap_rows": 0,
        "ignored_guardian_zero_anomaly_rows": 0,
        "student_self_identity_rows": 0,
        "ignored_no_transport_rows": 0,
    })
    source_metrics = {
        source["key"]: {
            **_new_row_metrics(len(source["rows"])),
            "invalid_email_rows": 0,
            "invalid_email_values": 0,
            "ignored_placeholder_rows": 0,
            "ignored_guardian_student_overlap_rows": 0,
            "ignored_guardian_zero_anomaly_rows": 0,
            "student_self_identity_rows": 0,
            "ignored_no_transport_rows": 0,
            "not_processed_rows": len(source["rows"]) if not students else 0,
        }
        for source in parsed_contact_sources
    }
    contact_metrics["not_processed_rows"] = total_contact_rows if not students else 0
    for source in (parsed_contact_sources if students else []):
        contact_map = source["mapping"]
        current_metrics = source_metrics[source["key"]]
        for row_number, row in source["rows"]:
            student_number = normalize_identifier(
                _first(row, contact_map, "student_number"))
            student_number_errors = []
            if not student_number:
                student_number_errors.append("student_number is required")
            elif not IDENTIFIER_RE.fullmatch(student_number):
                student_number_errors.append(
                    "student_number contains unsupported characters")
            if student_number_errors:
                current_metrics["rejected_rows"] += 1
                contact_metrics["rejected_rows"] += 1
                row_issues.append({
                    "file": source["key"], "row_number": row_number,
                    "classification": "rejected",
                    "errors": student_number_errors,
                })
                continue
            if student_number not in students:
                current_metrics["ignored_rows"] += 1
                current_metrics["ignored_no_transport_rows"] += 1
                contact_metrics["ignored_rows"] += 1
                contact_metrics["ignored_no_transport_rows"] += 1
                continue

            contact_ids = []
            for value in _values(row, contact_map, "contact_id"):
                normalized_contact_id = normalize_identifier(value)
                if normalized_contact_id:
                    contact_ids.append(normalized_contact_id)
            contact_id = next(
                (value for value in contact_ids if value != "0"),
                contact_ids[0] if contact_ids else "",
            )
            raw_relationship = normalize_text(
                _first(row, contact_map, "relationship"), 40).lower()

            if not contact_id and not _contact_has_payload(row, contact_map):
                current_metrics["ignored_rows"] += 1
                current_metrics["ignored_placeholder_rows"] += 1
                contact_metrics["ignored_rows"] += 1
                contact_metrics["ignored_placeholder_rows"] += 1
                continue

            is_split_student = bool(
                split_contacts and source["force_relationship"] == "student")
            is_split_guardian = bool(
                split_contacts and not source["force_relationship"]
                and source["default_relationship"] == "guardian")
            if contact_id == "0" and is_split_student:
                # BrightArrow uses zero for the student's own contact in this
                # export. A constant semantic key, scoped by student_number by
                # the importer, is stable and contains no contact PII.
                contact_id = STUDENT_SELF_CONTACT_ID
                current_metrics["student_self_identity_rows"] += 1
                contact_metrics["student_self_identity_rows"] += 1
            elif contact_id == "0" and is_split_guardian:
                # The guardian export also contains the student-self row. Do
                # not relabel the reserved zero sentinel as a guardian or let
                # it collide with the corresponding Student Contacts export.
                current_metrics["ignored_rows"] += 1
                current_metrics["ignored_guardian_student_overlap_rows"] += 1
                contact_metrics["ignored_rows"] += 1
                contact_metrics["ignored_guardian_student_overlap_rows"] += 1
                if raw_relationship:
                    current_metrics["ignored_guardian_zero_anomaly_rows"] += 1
                    contact_metrics["ignored_guardian_zero_anomaly_rows"] += 1
                    current_metrics["warning_rows"] += 1
                    contact_metrics["warning_rows"] += 1
                    row_issues.append({
                        "file": source["key"], "row_number": row_number,
                        "classification": "warning",
                        "errors": [
                            "guardian row used reserved contact_id 0 with a "
                            "relationship and was safely ignored"
                        ],
                    })
                continue

            email, invalid_emails = normalize_email_values(
                _values(row, contact_map, "email"))
            relationship = raw_relationship
            if source["force_relationship"]:
                relationship = source["force_relationship"]
            elif not relationship:
                relationship = source["default_relationship"]
            contact = {
                "contact_id": contact_id,
                "first_name": normalize_text(
                    _first(row, contact_map, "first_name"), 80),
                "last_name": normalize_text(
                    _first(row, contact_map, "last_name"), 80),
                "relationship": relationship,
                "email": email,
                "phone": normalize_phone_values(
                    _values(row, contact_map, "phone")),
                "notification_preference": normalize_text(
                    _first(row, contact_map, "notification_preference"), 40).lower(),
                "priority": normalize_text(
                    _first(row, contact_map, "priority"), 20),
            }
            email_warning = None
            if invalid_emails:
                email_warning = (
                    "invalid email value(s) were omitted; valid contact data "
                    "was retained"
                )
                current_metrics["invalid_email_rows"] += 1
                current_metrics["invalid_email_values"] += len(invalid_emails)
                contact_metrics["invalid_email_rows"] += 1
                contact_metrics["invalid_email_values"] += len(invalid_emails)
            errors = []
            if not contact_id:
                errors.append("contact_id is required; PII cannot be used as identity")
            elif not IDENTIFIER_RE.fullmatch(contact_id):
                errors.append("contact_id contains unsupported characters")
            if len(email) > 500:
                errors.append("normalized email addresses exceed the 500-character limit")
            if not contact["first_name"] and not contact["email"] and not contact["phone"]:
                errors.append("contact has no usable name, email, or phone")
            if errors:
                current_metrics["rejected_rows"] += 1
                contact_metrics["rejected_rows"] += 1
                row_issues.append({
                    "file": source["key"], "row_number": row_number,
                    "classification": "rejected", "errors": errors,
                })
                continue
            if invalid_emails:
                current_metrics["warning_rows"] += 1
                contact_metrics["warning_rows"] += 1
                row_issues.append({
                    "file": source["key"], "row_number": row_number,
                    "classification": "warning",
                    "errors": [email_warning],
                })
            key = (student_number, contact_id)
            canonical = json.dumps(contact, sort_keys=True, separators=(",", ":"))
            if key in seen_contacts:
                classification = (
                    "duplicate" if seen_contacts[key] == canonical else "conflict")
                row_issues.append({
                    "file": source["key"], "row_number": row_number,
                    "classification": classification,
                    "errors": [f"contact_id is repeated with {classification} values"],
                })
                current_metrics[classification + "_rows"] += 1
                contact_metrics[classification + "_rows"] += 1
                if classification == "conflict":
                    students[student_number].setdefault("conflicts", []).append(
                        "contacts contain conflicting values for one contact_id")
                continue
            seen_contacts[key] = canonical
            students[student_number]["contacts"].append(contact)
            current_metrics["accepted_rows"] += 1
            contact_metrics["accepted_rows"] += 1

    normalized = []
    for student_number in sorted(students):
        proposal = students[student_number]
        distinct_routes = {
            (item["route_prefix"], item["route_number"])
            for item in proposal["assignments"]
        }
        if not is_transportation_v2 and len(distinct_routes) != 1:
            proposal.setdefault("conflicts", []).append(
                "student has assignments for more than one bus route")
        proposal["assignments"] = sorted(
            proposal["assignments"],
            key=lambda item: (item["route_prefix"], item["route_number"], item["period"]),
        )
        proposal["contacts"] = sorted(
            proposal["contacts"], key=lambda item: item["contact_id"])
        normalized.append(proposal)

    if split_contacts:
        digest_input = bytearray(b"powerschool-split-contacts-v1\0")
        digest_input.extend(hashlib.sha256(transport_payload).digest())
        for source in parsed_contact_sources:
            digest_input.extend(source["key"].encode("utf-8"))
            digest_input.extend(b"\0")
            digest_input.extend(hashlib.sha256(source["payload"]).digest())
        combined_sha = hashlib.sha256(bytes(digest_input)).hexdigest()
    else:
        combined_sha = hashlib.sha256(
            hashlib.sha256(transport_payload).digest()
            + hashlib.sha256(contacts_payload).digest()
        ).hexdigest()
    file_metadata = {
        "transportation": {
            "headers": transport_headers,
            "rows": len(transport_rows),
            "contract": transport_contract,
        },
    }
    for source in parsed_contact_sources:
        file_metadata[source["key"]] = {
            "headers": source["headers"], "rows": len(source["rows"]),
        }
    if transportation_metrics["rejected_rows"]:
        preflight["warnings"].append(
            f'{transportation_metrics["rejected_rows"]} Transportation row(s) '
            "could not be normalized."
        )
    if transportation_metrics["ignored_rows"]:
        preflight["warnings"].append(
            f'{transportation_metrics["ignored_rows"]} Transportation route '
            "field value(s) were blank or a known non-bus category and were "
            "safely ignored."
        )
    if transportation_metrics["ignored_missing_period_rows"]:
        preflight["warnings"].append(
            f'{transportation_metrics["ignored_missing_period_rows"]} routed '
            "Transportation row(s) had no explicit AM/MD/PM period and were "
            "safely ignored instead of being assigned to every period."
        )
    invalid_directional_routes = (
        transportation_metrics["invalid_route_am_rows"]
        + transportation_metrics["invalid_route_pm_rows"]
    )
    if invalid_directional_routes:
        preflight["warnings"].append(
            f"{invalid_directional_routes} populated directional route field "
            "value(s) could not be normalized; any valid opposite-period "
            "assignment was retained."
        )
    directional_period_conflicts = (
        transportation_metrics["route_am_period_conflict_rows"]
        + transportation_metrics["route_pm_period_conflict_rows"]
    )
    if directional_period_conflicts:
        preflight["warnings"].append(
            f"{directional_period_conflicts} directional route field value(s) "
            "had a contradictory suffix and were safely ignored; any valid "
            "opposite-period assignment was retained."
        )
    if transportation_metrics["different_am_pm_route_rows"]:
        preflight["warnings"].append(
            f'{transportation_metrics["different_am_pm_route_rows"]} student '
            "row(s) use different AM and PM buses; both assignments were "
            "preserved."
        )
    if is_transportation_v2:
        preflight["warnings"].append(
            "Transportation v2 uses the district Students Combined dual-route "
            "contract. Delta is the safe default; Full Snapshot requires both "
            "AM and PM assignments and zero invalid-route or directional-period "
            "conflict rows."
        )
    if students and not contact_metrics["accepted_rows"]:
        preflight["warnings"].append(
            "No usable contact rows were accepted; transportation records may "
            "still be reviewed, but no recipient contact data will be updated."
        )
    if contact_metrics["ignored_placeholder_rows"]:
        preflight["warnings"].append(
            f'{contact_metrics["ignored_placeholder_rows"]} empty contact '
            "placeholder row(s) were safely ignored."
        )
    if contact_metrics["ignored_guardian_student_overlap_rows"]:
        preflight["warnings"].append(
            f'{contact_metrics["ignored_guardian_student_overlap_rows"]} '
            "student-self row(s) repeated in Guardian Contacts were safely ignored."
        )
    if contact_metrics["ignored_guardian_zero_anomaly_rows"]:
        preflight["warnings"].append(
            f'{contact_metrics["ignored_guardian_zero_anomaly_rows"]} Guardian '
            "Contacts row(s) used reserved contact_id 0 with a relationship; "
            "they were ignored and should be reviewed in PowerSchool."
        )
    if contact_metrics["ignored_no_transport_rows"]:
        preflight["warnings"].append(
            f'{contact_metrics["ignored_no_transport_rows"]} contact row(s) '
            "were outside the valid Transportation scope and were safely ignored."
        )
    if contact_metrics["invalid_email_rows"]:
        preflight["warnings"].append(
            f'{contact_metrics["invalid_email_rows"]} contact row(s) contained '
            "invalid email values; invalid values were omitted."
        )
    metrics = {
        "transportation": transportation_metrics,
        "contacts": contact_metrics,
        "contact_sources": source_metrics,
    }
    return {
        "students": normalized,
        "issues": row_issues,
        "files": file_metadata,
        "metrics": metrics,
        "preflight": preflight,
        "normalizer_revision": NORMALIZER_REVISION,
        "combined_sha256": combined_sha,
    }


def canonical_plan_hash(batch_public_id, schema_version, rows):
    canonical = [
        {
            "id": row["id"], "row_hash": row["row_hash"],
            "classification": row["classification"], "selected": bool(row["selected"]),
        }
        for row in sorted(rows, key=lambda item: item["id"])
    ]
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(
        f"powerschool:{schema_version}:{batch_public_id}:{payload}".encode("utf-8")
    ).hexdigest()


def safe_csv_cell(value):
    text = str(value or "")
    if text.lstrip("\x00\t\r\n ").startswith(("=", "+", "-", "@")):
        return "'" + text
    return text
