# Phase 3 rollout checklist

## Pre-deployment

- [ ] Target commit, `origin/main` and clean worktree recorded.
- [ ] Full tests, syntax, template compilation, dependency audit and security diff scan pass.
- [ ] Private PostgreSQL dump, environment and service-unit backups created and checksummed.
- [ ] Dump restored into an isolated database and all additive tables verified.
- [ ] Four-worker candidate passes authenticated smoke, apply, repeat-apply and rollback tests.
- [ ] `POWERSCHOOL_IMPORT_ENABLED=0` in production environment.

## Disabled deployment

- [ ] Fast-forward only; one controlled service restart.
- [ ] Existing public/admin/Legacy CSV flows pass.
- [ ] PowerSchool route returns 404 while disabled.
- [ ] Service, worker, header, CSP, database-connection and error-log gates pass.

## Pilot activation

- [ ] Explicit `import.powerschool` and, if required, `import.rollback` capability assigned.
- [ ] Feature flag enabled and one controlled restart completed.
- [ ] Synthetic/anonymized batch reconciles without persisting production records.
- [ ] One-school real pilot reviewed and approved by the operator.
- [ ] `selected + excluded + rejected = total` and PowerSchool source counts agree.
- [ ] Repeated apply creates zero duplicates; compensating rollback is verified.
- [ ] No automatic deactivation and no manual record overwritten.

## Rollback

- Feature problem without applied data: set the flag to `0` and restart.
- Applied batch: use the batch rollback while the retention window is open.
- Binary regression: return to the prior application revision; additive tables remain.
- Database disaster only: restore the verified private dump.

Record evidence at deployment, 24 hours, 72 hours and seven days.
