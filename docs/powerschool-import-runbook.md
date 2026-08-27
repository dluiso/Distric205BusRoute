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

The source data is not assumed to be clean merely because the CSV header is valid.
BrightArrow contact sources can return sentinel contact IDs, pseudo-contact rows and
empty placeholders. Transportation can also return a header plus no usable assignments.
Keep the three exports separate and let the importer apply source-specific rules; never
invent IDs, delete rows, merge files, or repair PII by hand.

### Transportation

The saved template and downloadable `powerschool-transportation-v1.csv` header use this
exact 10-column contract, in order:

1. `TRANSPORTATION.student_number`
2. `TRANSPORTATION.student_dcid`
3. `TRANSPORTATION.studentfname`
4. `TRANSPORTATION.studentlname`
5. `TRANSPORTATION.schoolid`
6. `TRANSPORTATION.grade_level`
7. `TRANSPORTATION.busnumber`
8. `TRANSPORTATION.stopnumber`
9. `TRANSPORTATION.fromto`
10. `TRANSPORTATION.ride_on_enabledToday`

`student_number` and a normalizable `busnumber` are required in every usable assignment.
`fromto` should resolve to AM, MD or PM. A student may have multiple rows for multiple
periods, but incompatible routes are classified as conflicts.

### Blocking Transportation preflight

Before a batch is staged, the application checks the Transportation population, usable
student numbers, normalizable bus assignments and rejected-row counts. Zero usable
Transportation assignments is a blocking failure, even if both contact files contain
thousands of rows. Correct the PowerSchool/BrightArrow source export first; do not select
Complete district snapshot and do not try to bypass the failure by renaming or editing a
CSV.

The supplied v1 profile also recognizes the district's verified `STUDENTS.*`,
`TRANSPORTATION.*` and `BRIGHTARROW.*` header aliases. A student may have multiple
rows for multiple periods, but conflicting bus routes are classified as `conflict`.

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

The v1 mapping accepts the verified `BRIGHTARROW.600_*`, `601_*`–`609_*` and
`801_*`–`803_* aliases. Repeated non-sentinel stable IDs with different values remain
conflicts. Invalid email values are surfaced for review; they are never silently converted
into a different destination.

## Annual workflow

1. Confirm the active bus catalog and its AM/MD/PM schedule assignments.
2. Run the three saved templates for the same school year and scope; do not edit or
   manually combine their CSV output.
3. Open **Notifications → PowerSchool Guide** for the operational checklist, then open
   the importer and choose the mapping version.
4. Select `Delta` for normal incremental work. Select `Complete district snapshot`
   only when the files are known to contain the entire district population.
5. Keep `PowerSchool saved templates` selected and upload Transportation, Student
   Contacts and Guardian Contacts. Analyze. The server first runs blocking preflight,
   then validates UTF-8, MIME/type, exact mapped headers, cumulative column/row limits,
   stable identifiers, duplicate files, duplicate rows, routes, periods and contacts.
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
11. Resubmitting the same files presents two explicit choices. **Open existing analysis**
   loads the immutable earlier batch. **Re-analyze against current state** creates a new
   linked batch with current mappings and operational data. Neither choice imports data,
   changes the source CSVs or silently overwrites the prior analysis.
12. Monitor audit logs and notifications before expanding a pilot to more schools.

## Rapid go/no-go checklist

Before applying any batch, all answers must be **yes**:

- Were all three exports generated for the same district context, school year and date?
- Was Transportation exported before its source-day cutoff?
- Does Transportation contain a plausible row population and populated bus assignments?
- Did the blocking preflight pass with at least one usable Transportation assignment?
- Is the policy `Delta`, unless a separately reconciled full-district snapshot was approved?
- Were sentinel and placeholder metrics reviewed without manually changing source IDs?
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
6. Observe at 24 hours, 72 hours and seven days before district-wide use.
7. To disable the feature, set the flag back to `0` and restart. Existing staged/audit
   tables remain intact. Use batch rollback for imported records; use application rollback
   only for a binary regression.

The final production gate is **NO-GO** if a high/medium security issue remains, a backup
cannot be restored, the pilot counts differ, reimport creates duplicates, rollback fails,
or any implicit deactivation occurs.
