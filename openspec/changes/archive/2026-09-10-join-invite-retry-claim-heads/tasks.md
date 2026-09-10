## 1. Repair

- [x] 1.1 Reproduce the current-target populated public CLI failure.
- [x] 1.2 Preserve historical proof and add populated invite/account merge-only downgrade regressions.
- [x] 1.3 Append a no-op join without changing published history.

## 2. Verify

- [x] 2.1 Verify CLI/drift, affected invite/account/permission controls and hosted PostgreSQL selection.
- [x] 2.2 Run affected static/strict spec checks and independent Medium review.
- [x] 2.3 Sync and archive the locally verified change; retain publication, hosted verification and accepted monitoring handoff as delivery gates.

Candidatec6b45b7f passed13 migration/receipt checks,100 invite/account/permission/CSRF controls, static/strict spec checks and independent Medium review. All250 published migration files remain unchanged. PostgreSQL selection is verified; hosted execution and accepted handoff remain pending after publication.
