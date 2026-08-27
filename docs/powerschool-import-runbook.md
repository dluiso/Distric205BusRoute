# PowerSchool roster export and import runbook

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

The district PowerSchool account contains three active reusable Data Export Manager
templates plus the retained Transportation v1 diagnostic template.
Open them through **District Office → Special Functions → Importing & Exporting → Data
Export Manager → My Templates**. Export all three for the same scope and school year.
Use comma delimiter, CR/LF line endings, UTF-8, column headers and quoted values. Do not
open and resave the files in Excel: stable identifiers and leading zeroes must remain
unchanged. Column order does not matter because the versioned mapping profile resolves
column names and aliases.

| Saved template | Template ID | PowerSchool source | Recommended filename |
|---|---:|---|---|
| **`D205 BusRoute - Transportation v2`** (AM/PM route pair; Delta default) | **`941`** | `BrightArrow - Basic - Students Combined` | `D205_BusRoute_Transportation_v2.csv` |
| `D205 BusRoute - Student Contacts v1` | — | `BrightArrow - Basic - Students Combined` | `D205_BusRoute_Student_Contacts_v1.csv` |
| `D205 BusRoute - Guardian Contacts v1` | — | `BrightArrow - Basic - Parents Combined` | `D205_BusRoute_Guardian_Contacts_v1.csv` |
| `D205 BusRoute - Transportation v1` (legacy diagnostic) | — | `BrightArrow Transportation Current Day Bussing` | `D205_BusRoute_Transportation_v1.csv` |

Use Transportation v2/template 941 for the verified dual-route contract. The source
labels are misleading: `BRIGHTARROW.013_bus_route` is the AM assignment and
`BRIGHTARROW.014_bus_stop` is the PM assignment, not a physical stop. The saved template
must export them as `route_am` and `route_pm`. Keep `Delta` as the default. A Complete
district snapshot requires independent proof of AM and PM coverage plus reconciliation
of every route-pair anomaly. Transportation v1 is retained only to compare or diagnose
historical exports from the old source; do not use it in place of v2.

PowerSchool can restore its source-generated filename when a template is reopened; the
importer does not use the filename as identity. If needed, rename the downloaded file in
Finder without opening or resaving its contents. Never schedule these exports to email or
an unsecured/shared folder. They contain protected student and guardian information.

The source data is not assumed to be clean merely because the CSV header is valid.
BrightArrow contact sources can return sentinel contact IDs, pseudo-contact rows and
empty placeholders. Transportation can also return a header plus no usable assignments.
Keep the three exports separate and let the importer apply source-specific rules; never
invent IDs, delete rows, merge files, or repair PII by hand.

### Transportation

The saved template and downloadable `powerschool-transportation-v2.csv` header use this
exact 9-column canonical contract, in order:

1. `student_number`
2. `first_name`
3. `last_name`
4. `grade`
5. `transport_status`
6. `route_am`
7. `route_pm`
8. `school`
9. `student_id`

`student_number` and at least one normalizable route leg are required for a usable
student. `route_am` always means AM and `route_pm` always means PM; neither value needs a
period suffix. There is intentionally no `stop` column in v2 because the upstream
`.014_bus_stop` field is carrying the PM route.

Each route leg is normalized independently. A blank value or a non-bus category such as
`Walker`, `Door-to-Door` or `NTW` is ignored for that leg instead of becoming an invalid
assignment. The preflight metrics `period_am_rows` and `period_pm_rows` expose accepted
assignments. Missing, unparseable or inconsistent AM/PM pairs are kept visible through
the following metrics:

- `route_am_period_conflict_rows` and `route_pm_period_conflict_rows`: a value carries
  a suffix that contradicts its canonical AM or PM column; that route leg is ignored
  while a valid opposite-period leg is retained;
- `invalid_route_am_rows` and `invalid_route_pm_rows`: a nonblank/non-bus value cannot
  be normalized as a route; and
- `different_am_pm_route_rows`: AM and PM legitimately differ. Both assignments are
  preserved; the count is informational and must be reconciled, not treated as a route
  conflict by itself.

Period conflicts and invalid values are route-pair anomalies. They must be resolved, and
the differing-route count must be reconciled, before Full Snapshot can be considered.

The old downloadable `powerschool-transportation-v1.csv` 10-column header is retained
only for historical comparison and diagnosis. Its old `BrightArrow Transportation
Current Day Bussing` source has produced route-empty output and is not the recommended
operational source.

### Blocking Transportation preflight

Before a batch is staged, the application checks the Transportation population, usable
student numbers, independently normalizable AM and PM legs, route-pair anomalies and
rejected-row counts. Zero usable Transportation assignments is a blocking failure, even
if both contact files contain thousands of rows. Do not bypass the failure by renaming or
editing a CSV.

The mapping profile recognizes the canonical v2 pair and the verified BrightArrow source
fields. Period counters describe the analyzed file only; they do not by themselves prove
complete district coverage. Full Snapshot remains NO-GO until AM and PM populations are
independently reconciled and all route-pair anomalies are resolved.

### Student and guardian contacts

Both saved contact templates use the same exact 18 selected columns, in order:

1. `BRIGHTARROW.003_student_number`
2. `BRIGHTARROW.600_00_contact_id`
3. `BRIGHTARROW.600_04_contact_std_detailid`
4. `BRIGHTARROW.600_01_contact_firstname`
5. `BRIGHTARROW.600_02_contact_lastname`
6. `BRIGHTARROW.600_03_contact_relationship`
7. `BRIGHTARROW.601_01_home_phone`
8. `BRIGHTARROW.602_01_phone2`
9. `BRIGHTARROW.603_01_phone3`
10. `BRIGHTARROW.604_01_phone4`
11. `BRIGHTARROW.605_01_phone5`
12. `BRIGHTARROW.606_01_phone6`
13. `BRIGHTARROW.607_01_phone7`
14. `BRIGHTARROW.608_01_phone8`
15. `BRIGHTARROW.609_01_phone9`
16. `BRIGHTARROW.801_email1`
17. `BRIGHTARROW.802_email2`
18. `BRIGHTARROW.803_email3`

The downloadable contracts are `powerschool-student-contacts-v1.csv` and
`powerschool-guardian-contacts-v1.csv`. The older `powerschool-contacts-v1.csv` remains
only for the legacy combined-file compatibility mode.

Rows from `Students Combined` are assigned the student role. Its source may emit
`contact_id=0`; the normalizer derives a deterministic direct-student identity from the
file role and stable student number, never from email, phone or name. `Parents Combined`
preserves the relationship, but reserves `contact_id=0` for overlapping student-self
rows and never imports it as a guardian. A nonblank relationship on that reserved ID is
reported as an anomaly. Placeholder rows containing only a student number are also
ignored. These artifacts are reported in preflight metrics instead of becoming thousands
of rejected or conflicting contacts. Other guardian rows still require a stable identifier;
PII is never substituted as identity. No manual CSV merge is required.

Ignored source artifacts are not review rows and therefore are not part of the
`selected + excluded + rejected = total` reconciliation.

Contacts for students who have no valid v2 Transportation assignment are also ignored
in aggregate as `contacts.ignored_no_transport_rows`. They do not create individual
review/rejection rows and cannot attach non-riders to a bus by contact-file presence.

The v1 mapping accepts the verified `BRIGHTARROW.600_*`, `601_*`–`609_*` and
`801_*`–`803_* aliases. Repeated non-sentinel stable IDs with different values remain
conflicts. Invalid email values are surfaced for review; they are never silently converted
into a different destination.

## Annual workflow

1. Confirm the active bus catalog and its AM and PM schedule assignments.
2. Run Transportation v2/template 941 plus the unchanged Student Contacts v1 and
   Guardian Contacts v1 templates for the same school year and scope; do not edit or
   manually combine their CSV output.
3. Open **Notifications → PowerSchool Guide** for the operational checklist, then open
   the importer and choose the mapping version.
4. Select `Delta` by default. Select `Complete district snapshot` only after independently
   validating both AM and PM coverage and reconciling all route-pair anomalies.
5. Keep `PowerSchool saved templates` selected and upload Transportation, Student
   Contacts and Guardian Contacts. Analyze. The server first runs blocking preflight,
   then validates UTF-8, MIME/type, exact mapped headers, cumulative column/row limits,
   stable identifiers, duplicate files, duplicate rows, routes, periods and contacts.
6. Review `period_am_rows`, `period_pm_rows`, both period-conflict counters, both invalid
   route counters and `different_am_pm_route_rows`, plus every `new`, `update`,
   `unchanged`, `duplicate`, `conflict` and `rejected` classification. Filter by
   classification, school, grade, group/route, or change/error type.
7. Include or exclude rows and save the selection. This regenerates `plan_hash`; any
   stale browser plan is rejected.
8. For a separately validated Complete district snapshot, deactivation candidates remain
   unselected and require explicit approval. Absence alone never selects them.
9. Apply the exact plan. The batch first revalidates target state and then applies all
   selected changes in one transaction. A failure changes no operational records.
10. Download the final CSV report and reconcile:
    `selected + excluded + rejected = total`.
11. Resubmitting the same files presents two explicit choices. **Open existing analysis**
   loads the immutable earlier batch. **Re-analyze against current state** creates a new
   linked batch with current mappings and operational data. Neither choice imports data,
   changes the source CSVs or silently overwrites the prior analysis.
12. Monitor audit logs and notifications before expanding a pilot to more schools.

## Rapid go/no-go checklist

Before applying any batch, all answers must be **yes**:

- Were all three exports generated for the same district context, school year and date?
- Was Transportation produced by v2/template 941 from
  `BrightArrow - Basic - Students Combined`?
- Does Transportation contain plausible AM and PM assignments after safe source exclusions?
- Did the blocking preflight pass, and were AM/PM and route-pair anomaly metrics reconciled?
- Is the policy `Delta`, unless both period populations and all anomalies were independently
  reconciled for a Complete district snapshot?
- Were non-rider, out-of-Transportation contact, sentinel and placeholder metrics
  reviewed without manually changing source IDs?
- Do selected, excluded and rejected totals reconcile exactly?
- Was the final plan reviewed by the authorized operator before Apply?

Any **no** is a NO-GO. Generate a corrected export or investigate the source; do not
transform the CSV to make it pass.

## Classification and preservation rules

- `new`: no PowerSchool student identity exists locally.
- `update`: a mapped enrollment/contact differs from the normalized proposal.
- `unchanged`: the mapped target already matches.
- `duplicate`: repeated source assignment or identical stable contact identity.
- `conflict`: incompatible routes, identities, contact values or target ownership.
- `rejected`: missing/invalid stable IDs, encoding, route, contact, or structural data.
- `deactivate_candidate`: present only for an independently validated Complete district
  snapshot and never selected automatically.

Manual contacts without PowerSchool identities are preserved. The importer never matches
or overwrites a person using a name, address, email, phone number or household label.

## Rollback

Rollback is compensating and batch-scoped. Before reversing anything, the application
compares every imported target to its recorded after-state. If an operator or a later
batch changed a target, rollback fails closed without changing any record. Otherwise it:

- deletes identities, contacts, enrollments and groups created by the batch;
- restores the exact prior values for updates and approved deactivations;
- preserves notification/outbox history while detaching references to removed imported
  subscribers and groups;
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
6. Observe at 24 hours, 72 hours and seven days before district-wide use. Do not use
   Complete district snapshot until AM/PM coverage and route-pair anomalies reconcile.
7. To disable the feature, set the flag back to `0` and restart. Existing staged/audit
   tables remain intact. Use batch rollback for imported records; use application rollback
   only for a binary regression.

The final production gate is **NO-GO** if a high/medium security issue remains, a backup
cannot be restored, the pilot counts differ, reimport creates duplicates, rollback fails,
or any implicit deactivation occurs.
