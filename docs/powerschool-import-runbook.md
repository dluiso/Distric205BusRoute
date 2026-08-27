# PowerSchool roster export and import runbook

PowerSchool Import v1 is a feature-flagged workflow. Routine Delta batches are additive
and never treat absence from an upload as permission to deactivate a subscriber. The
first PowerSchool migration can also perform a separately approved, provenance-bound
cutover from Legacy CSV v1: it deactivates only subscribers proven to have been created
by applied Legacy CSV batches, never manual subscribers. That cutover is prohibited in
Delta and requires a validated Complete district snapshot.

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

## First PowerSchool batch: Legacy CSV cutover

The first applicable PowerSchool batch detects legacy subscribers by import provenance:
the application uses the applied Legacy CSV batch and change history that created them.
It does not infer ownership from names, household labels, email addresses or phone
numbers. Subscribers with explicit manual provenance remain active. Any active subscriber
without proven Legacy or manual provenance blocks selection and Apply until ownership is
reconciled; the application never assumes that an unclassified row is manual.

A large `new` count does **not** mean the notification database is empty. `new` means the
student number has no PowerSchool `ExternalIdentity` yet. The same student or household
can therefore already exist as a Legacy CSV subscriber and still appear as `new` in the
first PowerSchool analysis. The cutover prevents those proven legacy records from
remaining active beside the newly linked PowerSchool subscribers.

### Historical pre-audit Legacy CSV provenance baseline

Some production Legacy CSV rosters may predate the audit records that identify which
subscriber, contact and group rows the importer created. The application fails closed
when active subscribers lack explicit provenance: no PowerSchool cutover may be approved
or applied until an authorized operator adopts a verified historical baseline. This is
not permission to infer ownership from PII or to classify every existing subscriber as
legacy.

The ordinary cutover relies exclusively on recorded import provenance and never infers
ownership from names, household labels, email addresses, phone numbers or other PII.
Baseline adoption is a narrowly bounded, one-time recovery exception: it reconciles the
exact known historical CSV to current records using full-record equality under the
historical importer rules. It is not fuzzy identity matching. Any missing or ambiguous
match blocks the entire adoption.

Baseline adoption requires the **exact original Legacy combined CSV** used to create the
active roster. Do not reconstruct it from the database, edit it, normalize it, resave it
in Excel or substitute a later export. Keep the file private and run the following from
the application environment. The initial command is always a dry run:

```bash
flask --app app adopt-legacy-baseline /absolute/path/to/original.csv
```

The dry run prints only aggregate candidate, contact, group and preserved counts plus a
source SHA and manifest SHA. It must not print or log names, student identifiers, email
addresses, phone numbers or other PII. Reconcile all aggregates independently. Then run
the apply command against the exact same source, copying every reported value literally:

```bash
flask --app app adopt-legacy-baseline /absolute/path/to/original.csv \
  --apply \
  --source-sha SOURCE_SHA_FROM_DRY_RUN \
  --manifest-sha MANIFEST_SHA_FROM_DRY_RUN \
  --expected-candidates CANDIDATE_COUNT \
  --expected-contacts CONTACT_COUNT \
  --expected-groups GROUP_COUNT \
  --expected-preserved PRESERVED_COUNT \
  --approved-by ACTIVE_ADMIN_USERNAME
```

`ACTIVE_ADMIN_USERNAME` must identify an active administrator. The command rechecks the
source SHA, manifest SHA and all four aggregate counts at apply time. A changed source,
hash or count, missing match, ambiguous match or unauthorized approver blocks the entire
adoption without partial changes. Do not weaken the match or fill gaps manually.

For the known District 205 archived source, the independently recorded operational
checkpoint is:

| Evidence | Expected value |
|---|---:|
| Source SHA | `dafbffbfc40d6359ee6feb20e86c45982bab7efa8f64980cd8cab713e4a6ddd7` |
| Candidates | `1852` |
| Contacts | `4200` |
| Groups | `68` |
| Preserved | `1` |

These values are documentation and operator evidence for that known source; they are not
application defaults. Copying hashes and counts from the same dry run into its apply
command pins the attempted operation, but does **not** independently prove that the
source or classification is correct. Before approval, compare the dry-run source SHA and
all four counts to this independent checkpoint and the source-custody record. Any
difference is a NO-GO and must be investigated rather than overridden.

An already staged Delta batch—including the current pre-baseline analysis—is invalid for
the first cutover and must never be applied. After baseline adoption succeeds, upload the
current Transportation v2, Student Contacts and Guardian Contacts exports again and run
**Re-analyze against current state** as a new `full_district` (**Complete district
snapshot**) batch. Only that fresh analysis can proceed to the remaining cutover gates.

When the analysis reports that a Legacy CSV cutover is required, Apply remains blocked
until all of these conditions are satisfied:

1. The policy is `full_district` (**Complete district snapshot**) and Transportation is
   the approved v2/template 941 dual-route export, with district-wide AM and PM coverage
   validated. A Legacy CSV cutover is prohibited in `delta`. Transportation v1 remains
   blocked for cutover even if its batch is marked Full; the policy label cannot upgrade
   the legacy single-route contract. Delta remains the normal safe policy for subsequent
   PowerSchool updates.
2. The batch is a current analysis of the present application state. Any PowerSchool
   batch staged before the cutover protection was deployed, or otherwise marked as
   requiring reanalysis, must use **Re-analyze against current state**. Opening an older
   staged batch is useful for inspection only; it is not an approval shortcut.
3. The review has zero `conflict` rows and zero `rejected` rows. Correct the source or
   configuration and re-analyze; never exclude an error merely to unlock the cutover.
4. Every importable `new` and `update` row remains selected. An empty or partial
   importable selection blocks the cutover. If any importable proposal should not proceed,
   correct its source or configuration and re-analyze instead of excluding it.
5. The operator reconciles the displayed legacy candidate count, explicitly approves
   the Legacy CSV cutover checkbox and saves the selection so the approval is bound to
   the regenerated `plan_hash`.

Apply then performs one atomic transaction: it applies the selected PowerSchool changes
and deactivates **all** subscribers proven to have been created by applied Legacy CSV
batches. It preserves manual subscribers. If any selected change or cutover operation
fails, none of them are committed. This one-time provenance cutover is distinct from
absence-based deactivation and cannot accompany a Delta batch.

The cutover is recorded as part of the PowerSchool batch and is rollbackable within the
normal rollback retention window. A successful batch rollback reverses the selected
PowerSchool changes and restores the prior active state of its legacy cutover candidates,
provided the usual later-edit safety check passes.

Once a PowerSchool roster is active, Legacy CSV is disabled as a roster source. Do not
use Legacy CSV to add, replace or refresh subscribers after the cutover. All later roster
updates and new-year imports must use PowerSchool Import so stable `ExternalIdentity`
links, preview classifications, atomic apply and rollback protections remain intact.

## Annual workflow

1. Confirm the active bus catalog and its AM and PM schedule assignments.
2. Run Transportation v2/template 941 plus the unchanged Student Contacts v1 and
   Guardian Contacts v1 templates for the same school year and scope; do not edit or
   manually combine their CSV output.
3. Open **Notifications → PowerSchool Guide** for the operational checklist, then open
   the importer and choose the mapping version.
4. Select `Delta` by default for routine updates. A first Legacy CSV cutover is the
   exception: it is prohibited in Delta and requires `Complete district snapshot` after
   independently validating both AM and PM coverage and reconciling all route-pair anomalies.
5. Keep `PowerSchool saved templates` selected and upload Transportation, Student
   Contacts and Guardian Contacts. Analyze. The server first runs blocking preflight,
   then validates UTF-8, MIME/type, exact mapped headers, cumulative column/row limits,
   stable identifiers, duplicate files, duplicate rows, routes, periods and contacts.
6. Review `period_am_rows`, `period_pm_rows`, both period-conflict counters, both invalid
   route counters and `different_am_pm_route_rows`, plus every `new`, `update`,
   `unchanged`, `duplicate`, `conflict` and `rejected` classification. Filter by
   classification, school, grade, group/route, or change/error type.
7. Remember that `new` means no PowerSchool `ExternalIdentity`; it does not mean the
   database is empty or prove that no Legacy CSV version of the subscriber exists.
8. If the importer reports a missing historical Legacy provenance baseline, stop. Use
   the exact original Legacy combined CSV with the documented dry-run command. Reconcile
   its PII-safe counts and hashes, then use the fully pinned apply command with an active
   administrator as approver. Any missing or ambiguous match remains a blocker. After
   success, upload all three current exports again and create a new Complete district
   snapshot; the current or any staged Delta analysis is invalid for cutover.
9. If the Legacy CSV cutover banner appears after any required baseline adoption succeeds,
   require the approved Transportation v2
   dual-route export in a Complete district snapshot, current reanalysis, zero conflicts,
   zero rejected rows and reconciliation of its provenance-derived candidate count.
   Transportation v1 cannot authorize cutover even when marked Full.
   Keep all importable `new` and `update` rows selected: an empty or partial selection is
   blocked. Explicitly approve the atomic cutover; manual subscribers remain active.
10. Without a cutover, include or exclude importable rows according to the reviewed plan.
   With a cutover, retain the complete importable selection. Save the selection to
   regenerate `plan_hash`; any stale browser plan is rejected.
11. For a separately validated Complete district snapshot, deactivation candidates remain
   unselected and require explicit approval. Absence alone never selects them.
12. Apply the exact plan. The batch first revalidates target state and then applies all
   selected changes and any approved provenance cutover in one transaction. A failure
   changes no operational records.
13. Download the final CSV report and reconcile:
    `selected + excluded + rejected = total`.
14. Resubmitting the same files presents two explicit choices. **Open existing analysis**
   loads the immutable earlier batch. **Re-analyze against current state** creates a new
   linked batch with current mappings and operational data. Neither choice imports data,
   changes the source CSVs or silently overwrites the prior analysis.
15. Monitor audit logs and notifications before expanding a pilot to more schools.
16. After a PowerSchool roster becomes active, perform every later roster update through
    PowerSchool Import. Legacy CSV is no longer an allowed roster source.

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
- Is this a current analysis rather than an older staged batch that requires reanalysis?
- Are both the `conflict` and `rejected` counts zero?
- If historical Legacy provenance is missing, did the baseline dry run use the exact
  original combined CSV, were its PII-safe source/manifest hashes and all four aggregate
  counts reconciled, and did an active administrator approve the pinned apply? Any
  missing or ambiguous match is a NO-GO. A staged Delta remains invalid after adoption;
  create a new Complete district snapshot analysis.
- If a Legacy CSV cutover is required, is the policy Complete district snapshot rather
  than Delta, is Transportation the approved v2 dual-route export, and is district-wide
  AM/PM coverage proven? A Transportation v1 Full batch remains a NO-GO.
- If a Legacy CSV cutover is required, was its provenance-derived candidate count
  reconciled, are all importable `new`/`update` rows selected, and was its atomic
  deactivation explicitly approved and saved? An empty or partial selection is a NO-GO.
- Do selected, excluded and rejected totals reconcile exactly?
- Was the final plan reviewed by the authorized operator before Apply?

Any **no** is a NO-GO. Generate a corrected export or investigate the source; do not
transform the CSV to make it pass.

## Classification and preservation rules

- `new`: no PowerSchool student `ExternalIdentity` exists locally. It does not mean the
  database is empty and does not rule out an equivalent unlinked Legacy CSV subscriber.
- `update`: a mapped enrollment/contact differs from the normalized proposal.
- `unchanged`: the mapped target already matches.
- `duplicate`: repeated source assignment or identical stable contact identity.
- `conflict`: incompatible routes, identities, contact values or target ownership.
- `rejected`: missing/invalid stable IDs, encoding, route, contact, or structural data.
- `deactivate_candidate`: present only for an independently validated Complete district
  snapshot and never selected automatically.

Manual contacts without PowerSchool identities are preserved. The importer never matches
or overwrites a person using a name, address, email, phone number or household label.
The first cutover deactivates only subscribers whose applied Legacy CSV creation provenance
is recorded; manual subscribers are preserved.

After an active PowerSchool roster exists, Legacy CSV cannot be used as a roster source.
Use PowerSchool Import for every subsequent addition, correction and annual refresh.

## Rollback

Rollback is compensating and batch-scoped. Before reversing anything, the application
compares every imported target to its recorded after-state. If an operator or a later
batch changed a target, rollback fails closed without changing any record. Otherwise it:

- deletes identities, contacts, enrollments and groups created by the batch;
- restores the exact prior values for updates and approved deactivations;
- restores the prior active state of subscribers deactivated by an approved Legacy CSV
  cutover;
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
