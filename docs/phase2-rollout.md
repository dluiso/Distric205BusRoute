# Phase 2 rollout and rollback

Phase 2 is additive. The existing operational tables and Legacy CSV user flow remain
available; new authorization, broadcast-job, and import-staging tables are created at
startup under the existing database initialization lock.

## Required pre-deployment gates

1. Confirm the target commit, a clean worktree, and the current production revision.
2. Create a private PostgreSQL dump and record its checksum and restore command.
3. Copy the current environment and service unit with mode `0600`.
4. Restore the dump into an isolated database and start four Gunicorn workers against it.
5. Require the full test suite, template compilation, dependency audit, Compose validation,
   and security diff scan to pass.
6. Keep `POWERSCHOOL_IMPORT_ENABLED=0`.
7. Set `CSP_REPORT_ONLY=1` during the observation window while retaining
   `CSP_ENFORCE=1`; review violations before and after the restart.

## Deployment

1. Install exactly the pinned dependency set in a candidate virtual environment.
2. Start the candidate against the isolated restored database and run authenticated smoke tests.
3. Update the production checkout using a fast-forward only.
4. Restart the service once and verify all workers start without migration errors.
5. Verify `/health`, the public page, API, login, Dashboard, Buses, Status Types,
   Statistics, Users, Notifications, Configuration, Logs, and Profile.
6. Verify an operator with limited Notifications sees masked PII and cannot export it.
7. Analyze a synthetic Legacy CSV, confirm the immutable batch, and remove the synthetic
   records after validation.

## Rollback

The previous application version safely ignores the additive Phase 2 tables. Roll back
the checkout and virtual environment, restore the previous environment/service unit, and
restart. Do not drop the Phase 2 tables during an application rollback; they may contain
audit and rollback evidence. Restore the database dump only if existing operational data
was changed or corrupted, not merely because the new binary was reverted.

If an import was applied during validation, reverse only the recorded `ImportChange`
targets after verifying that no later edits depend on them. PowerSchool rollback is not
available or exposed until Phase 3 implements and validates the compensating workflow.
