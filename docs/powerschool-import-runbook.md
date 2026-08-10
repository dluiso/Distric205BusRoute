# PowerSchool annual import runbook

PowerSchool Import v1 is an additive, feature-flagged workflow. It does not replace
Legacy CSV v1 and it never treats absence from an upload as permission to delete or
deactivate a subscriber.

## Security and authorization

- The application must start with `POWERSCHOOL_IMPORT_ENABLED=1` before the page is
  reachable. The default is `0`.
- An operator needs the explicit `import.powerschool` capability. Notifications module
  access alone does not grant it.
- Compensating rollback additionally requires `import.rollback`.
- Operators without `notifications.pii` see masked names, student identifiers, email
  addresses and phone numbers in previews and reports.
- Uploaded bytes are written under the private instance import directory with mode
  `0600`; they are never placed in `static`.
- Raw files are purged after apply or stage expiry. Normalized PII and rollback snapshots
  are purged after `POWERSCHOOL_ROLLBACK_RETENTION_DAYS` (30 by default), after which an
  applied batch changes to `retention_closed` and cannot be rolled back.

## Saved PowerSchool exports

The district PowerSchool account contains three reusable Data Export Manager templates.
Open them through **District Office → Special Functions → Importing & Exporting → Data
Export Manager → My Templates**. Export all three for the same scope and school year.
Use comma delimiter, CR/LF line endings, UTF-8, column headers and quoted values. Do not
open and resave the files in Excel: stable identifiers and leading zeroes must remain
unchanged. Column order does not matter because the versioned mapping profile resolves
column names and aliases.

| Saved template | PowerSchool source | Expected filename |
|---|---|---|
| `D205 BusRoute - Transportation v1` | `BrightArrow Transportation Current Day Bussing` | `D205_BusRoute_Transportation_v1.csv` |
| `D205 BusRoute - Student Contacts v1` | `BrightArrow - Basic - Students Combined` | `D205_BusRoute_Student_Contacts_v1.csv` |
| `D205 BusRoute - Guardian Contacts v1` | `BrightArrow - Basic - Parents Combined` | `D205_BusRoute_Guardian_Contacts_v1.csv` |

Run Transportation before 6:00 PM because its PowerSchool source changes to the next
service day after 6:00 PM. Never schedule these exports to email or an unsecured/shared
folder. They contain protected student and guardian information.

### Transportation

Use the downloadable `powerschool-transportation-v1.csv` header as the preferred
contract:

| Canonical field | Required | Purpose |
|---|---:|---|
| `student_number` | yes | Stable student identity; never substitute name or email |
| `student_id` | no | PowerSchool internal/DCID reference |
| `household_id` | no | Stable source household identifier; defaults to student number |
| `first_name`, `last_name` | no | Display and notification contact values, not identity |
| `school`, `grade` | no | Preview filters and reconciliation |
| `route` | yes | Bus identifier and number, such as `MCK1` or `MCK 01` |
| `stop` | no | Review information |
| `period` | no | `AM`, `MD`, `PM` or a configured alias; may be embedded in route |
| `transport_status` | no | Source review information |
| `school_year` | no | Source check; the operator-entered year governs the batch |
| `source_id` | no | Stable transportation record identifier |

The supplied v1 profile also recognizes the district's verified `STUDENTS.*`,
`TRANSPORTATION.*` and `BRIGHTARROW.*` header aliases. A student may have multiple
rows for multiple periods, but conflicting bus routes are classified as `conflict`.

### Student and guardian contacts

Use `powerschool-contacts-v1.csv`:

| Canonical field | Required | Purpose |
|---|---:|---|
| `student_number` | yes | Joins to Transportation |
| `contact_id` | yes | Stable contact/DCID identity; PII is never used as identity |
| `first_name`, `last_name` | no | Display values |
| `relationship` | no | `student`/`self` creates or updates the student contact; others are guardians |
| `email`, `phone` | no | Normalized notification destinations |
| `notification_preference` | no | Retained in normalized review data |
| `priority` | no | Retained in normalized review data |

PowerSchool separates student contact information from parent/guardian contacts. Upload
both saved contact exports; the application combines them during normalization. Rows
from `Students Combined` are assigned the student role. `Parents Combined` preserves its
relationship and defaults a blank relationship to guardian. No manual CSV merge is
required.

The v1 mapping accepts the verified `BRIGHTARROW.600_*`, `601_*`–`609_*` and
`801_*`–`803_* aliases. Repeated stable IDs with different values are conflicts. The
downloadable `powerschool-contacts-v1.csv` remains available only as a backward-compatible
combined-file contract.

## Annual workflow

1. Confirm the active bus catalog and its AM/MD/PM schedule assignments.
2. Run the three saved templates for the same school year and scope; do not edit or
   manually combine their CSV output.
3. Open **Notifications → PowerSchool Guide** for the operational checklist, then open
   the importer and choose the mapping version.
4. Select `Delta` for normal incremental work. Select `Complete district snapshot`
   only when the files are known to contain the entire district population.
5. Keep `PowerSchool saved templates` selected and upload Transportation, Student
   Contacts and Guardian Contacts. Analyze. The server validates UTF-8, MIME/type,
   headers, cumulative column/row limits, stable identifiers, duplicate files, duplicate
   rows, routes, periods and contacts.
6. Review `new`, `update`, `unchanged`, `duplicate`, `conflict`, `rejected` and any
   `deactivate_candidate` rows. Filter by classification, school, grade, group/route,
   or change/error type.
7. Include or exclude rows and save the selection. This regenerates `plan_hash`; any
   stale browser plan is rejected.
8. For a complete snapshot, deactivation candidates remain unselected. Selecting any
   requires the separate deactivation approval checkbox.
9. Apply the exact plan. The batch first revalidates target state and then applies all
   selected changes in one transaction. A failure changes no operational records.
10. Download the final CSV report and reconcile:
    `selected + excluded + rejected = total`.
11. Reopening or resubmitting the same applied batch creates no duplicates. Exact file
   sets already staged or applied are detected by SHA-256.
12. Monitor audit logs and notifications before expanding a pilot to more schools.

## Classification and preservation rules

- `new`: no PowerSchool student identity exists locally.
- `update`: a mapped enrollment/contact differs from the normalized proposal.
- `unchanged`: the mapped target already matches.
- `duplicate`: repeated source assignment or identical stable contact identity.
- `conflict`: incompatible routes, identities, contact values or target ownership.
- `rejected`: missing/invalid stable IDs, encoding, route, contact, or structural data.
- `deactivate_candidate`: present only for complete district snapshots and never selected
  automatically.

Manual contacts without PowerSchool identities are preserved. The importer never matches
or overwrites a person using a name, address, email, phone number or household label.

## Rollback

Rollback is compensating and batch-scoped. Before reversing anything, the application
compares every imported target to its recorded after-state. If an operator or a later
batch changed a target, rollback fails closed without changing any record. Otherwise it:

- deletes identities, contacts, enrollments and groups created by the batch;
- restores the exact prior values for updates and approved deactivations;
- commits the reversal atomically; and
- writes a rollback audit event.

Do not restore the whole database for an ordinary batch reversal. Use the private database
backup only for disaster recovery.

## Production rollout and rollback

1. Deploy code and additive schema with `POWERSCHOOL_IMPORT_ENABLED=0`.
2. Restore the production backup into an isolated PostgreSQL database and run the full
   suite plus a four-worker authenticated smoke test.
3. Enable the flag for an explicitly authorized pilot operator.
4. Analyze an anonymized fixture, then a one-school pilot; compare counts to PowerSchool.
5. Apply only after the preview, report and audit events reconcile.
6. Observe at 24 hours, 72 hours and seven days before district-wide use.
7. To disable the feature, set the flag back to `0` and restart. Existing staged/audit
   tables remain intact. Use batch rollback for imported records; use application rollback
   only for a binary regression.

The final production gate is **NO-GO** if a high/medium security issue remains, a backup
cannot be restored, the pilot counts differ, reimport creates duplicates, rollback fails,
or any implicit deactivation occurs.
