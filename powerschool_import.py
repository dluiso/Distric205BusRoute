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
ROUTE_RE = re.compile(
    r"^([A-Z]+(?:\s+[A-Z]+)*)\s*0*([0-9]+)\s*(AM|MD|PM|A|P)?$",
    re.IGNORECASE,
)


DEFAULT_MAPPING_V1 = {
    "identity": "student_number",
    "files": {
        "transportation": {
            "required": ["student_number", "route"],
            "columns": {
                "student_number": [
                    "student_number", "STUDENTS.Student_Number",
                    "BRIGHTARROW.003_student_number",
                    "TRANSPORTATION.student_number",
                ],
                "student_id": [
                    "student_id", "STUDENTS.ID", "STUDENTS.dcid",
                    "TRANSPORTATION.StudentID", "TRANSPORTATION.student_dcid",
                ],
                "household_id": [
                    "household_id", "family_id", "source_identifier",
                    "STUDENTS.Family_Ident",
                ],
                "first_name": [
                    "first_name", "STUDENTS.First_Name",
                    "BRIGHTARROW.006_studentfname",
                    "TRANSPORTATION.studentfname",
                ],
                "last_name": [
                    "last_name", "STUDENTS.Last_Name",
                    "BRIGHTARROW.007_studentlname",
                    "TRANSPORTATION.studentlname",
                ],
                "school": [
                    "school", "school_id", "STUDENTS.SchoolID",
                    "BRIGHTARROW.200_schoolid", "TRANSPORTATION.SchoolID",
                    "TRANSPORTATION.schoolid",
                ],
                "grade": ["grade", "grade_level", "STUDENTS.Grade_Level"],
                "route": [
                    "route", "bus_route", "STUDENTS.Bus_Route",
                    "BRIGHTARROW.013_bus_route", "TRANSPORTATION.BusNumber",
                    "TRANSPORTATION.busnumber",
                ],
                "stop": [
                    "stop", "bus_stop", "STUDENTS.Bus_Stop",
                    "BRIGHTARROW.014_bus_stop", "TRANSPORTATION.StopNumber",
                    "TRANSPORTATION.stopnumber",
                ],
                "period": [
                    "period", "direction", "TRANSPORTATION.FromTo",
                    "TRANSPORTATION.fromto", "TRANSPORTATION.Type",
                    "TRANSPORTATION.type",
                ],
                "transport_status": [
                    "transport_status", "active", "ride_on_enabledToday",
                    "TRANSPORTATION.ride_on_enabledToday",
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
                    "contact_id", "contact_dcid",
                    "BRIGHTARROW.600_00_contact_id",
                    "BRIGHTARROW.600_04_contact_std_detailid",
                ],
                "first_name": [
                    "first_name", "contact_first_name",
                    "BRIGHTARROW.600_01_contact_firstname",
                ],
                "last_name": [
                    "last_name", "contact_last_name",
                    "BRIGHTARROW.600_02_contact_lastname",
                ],
                "relationship": [
                    "relationship", "role",
                    "BRIGHTARROW.600_03_contact_relationship",
                ],
                "email": [
                    "email", "BRIGHTARROW.801_email1",
                    "BRIGHTARROW.802_email2", "BRIGHTARROW.803_email3",
                ],
                "phone": [
                    "phone", "home_phone", "BRIGHTARROW.601_01_home_phone",
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


def _normalize_period(value, aliases):
    raw = normalize_text(value).upper()
    return aliases.get(raw)


def build_normalized_plan(transport_payload, contacts_payload, mapping, max_rows, max_columns):
    files = mapping.get("files") or {}
    if "transportation" not in files or "contacts" not in files:
        raise ImportValidationError("The selected mapping profile is incomplete.")

    transport_headers, transport_rows = read_csv_payload(
        transport_payload, max_rows, max_columns)
    contact_headers, contact_rows = read_csv_payload(
        contacts_payload, max_rows, max_columns)
    transport_map = resolve_mapping(transport_headers, files["transportation"])
    contact_map = resolve_mapping(contact_headers, files["contacts"])
    aliases = _period_alias_map(mapping)

    students = {}
    row_issues = []
    seen_transport = set()
    for row_number, row in transport_rows:
        student_number = normalize_identifier(_first(row, transport_map, "student_number"))
        route = normalize_route(_first(row, transport_map, "route"))
        period_raw = _first(row, transport_map, "period")
        period = _normalize_period(period_raw, aliases) if period_raw else None
        if route and not period:
            period = route.get("period")
        errors = []
        if not student_number:
            errors.append("student_number is required")
        elif not IDENTIFIER_RE.fullmatch(student_number):
            errors.append("student_number contains unsupported characters")
        if not route:
            errors.append("route is blank or cannot be normalized")
        if period_raw and not period:
            errors.append("period is not a configured AM/MD/PM alias")
        if errors:
            row_issues.append({
                "file": "transportation", "row_number": row_number,
                "classification": "rejected", "errors": errors,
            })
            continue
        route_key = f"{route['prefix']}|{route['number']}"
        duplicate_key = (student_number, route_key, period or "ALL")
        if duplicate_key in seen_transport:
            row_issues.append({
                "file": "transportation", "row_number": row_number,
                "classification": "duplicate",
                "errors": ["duplicate transportation assignment"],
            })
            continue
        seen_transport.add(duplicate_key)
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
        proposal["assignments"].append({
            "route_prefix": route["prefix"], "route_number": route["number"],
            "period": period or "ALL",
        })
        proposal["source_rows"].append(row_number)
        for field in ("first_name", "last_name", "school", "grade", "stop"):
            incoming = normalize_text(_first(row, transport_map, field))
            if incoming and proposal.get(field) and proposal[field] != incoming:
                proposal.setdefault("conflicts", []).append(
                    f"transportation rows disagree on {field}")
            elif incoming:
                proposal[field] = incoming

    seen_contacts = {}
    for row_number, row in contact_rows:
        student_number = normalize_identifier(_first(row, contact_map, "student_number"))
        contact_id = normalize_identifier(_first(row, contact_map, "contact_id"))
        email, invalid_emails = normalize_email_values(_values(row, contact_map, "email"))
        contact = {
            "contact_id": contact_id,
            "first_name": normalize_text(_first(row, contact_map, "first_name"), 80),
            "last_name": normalize_text(_first(row, contact_map, "last_name"), 80),
            "relationship": normalize_text(
                _first(row, contact_map, "relationship"), 40).lower(),
            "email": email,
            "phone": normalize_phone_values(_values(row, contact_map, "phone")),
            "notification_preference": normalize_text(
                _first(row, contact_map, "notification_preference"), 40).lower(),
            "priority": normalize_text(_first(row, contact_map, "priority"), 20),
        }
        errors = []
        if not student_number:
            errors.append("student_number is required")
        elif student_number not in students:
            errors.append("student_number has no valid transportation row")
        if not contact_id:
            errors.append("contact_id is required; PII cannot be used as identity")
        elif not IDENTIFIER_RE.fullmatch(contact_id):
            errors.append("contact_id contains unsupported characters")
        if invalid_emails:
            errors.append("one or more email addresses are invalid")
        if len(email) > 500:
            errors.append("normalized email addresses exceed the 500-character limit")
        if not contact["first_name"] and not contact["email"] and not contact["phone"]:
            errors.append("contact has no usable name, email, or phone")
        if errors:
            row_issues.append({
                "file": "contacts", "row_number": row_number,
                "classification": "rejected", "errors": errors,
            })
            continue
        key = (student_number, contact_id)
        canonical = json.dumps(contact, sort_keys=True, separators=(",", ":"))
        if key in seen_contacts:
            classification = "duplicate" if seen_contacts[key] == canonical else "conflict"
            row_issues.append({
                "file": "contacts", "row_number": row_number,
                "classification": classification,
                "errors": [f"contact_id is repeated with {classification} values"],
            })
            if classification == "conflict":
                students[student_number].setdefault("conflicts", []).append(
                    "contacts contain conflicting values for one contact_id")
            continue
        seen_contacts[key] = canonical
        students[student_number]["contacts"].append(contact)

    normalized = []
    for student_number in sorted(students):
        proposal = students[student_number]
        distinct_routes = {
            (item["route_prefix"], item["route_number"])
            for item in proposal["assignments"]
        }
        if len(distinct_routes) != 1:
            proposal.setdefault("conflicts", []).append(
                "student has assignments for more than one bus route")
        proposal["assignments"] = sorted(
            proposal["assignments"],
            key=lambda item: (item["route_prefix"], item["route_number"], item["period"]),
        )
        proposal["contacts"] = sorted(
            proposal["contacts"], key=lambda item: item["contact_id"])
        normalized.append(proposal)

    combined_sha = hashlib.sha256(
        hashlib.sha256(transport_payload).digest()
        + hashlib.sha256(contacts_payload).digest()
    ).hexdigest()
    return {
        "students": normalized,
        "issues": row_issues,
        "files": {
            "transportation": {"headers": transport_headers, "rows": len(transport_rows)},
            "contacts": {"headers": contact_headers, "rows": len(contact_rows)},
        },
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
