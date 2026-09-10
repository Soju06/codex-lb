## 1. Repair

- [x] 1.1 Pin actual main and reproduce populated public head-upgrade failure.
- [x] 1.2 Preserve historical tests and add populated role/user/receipt parent and merge-only downgrade regressions.
- [x] 1.3 Append the no-op join without modifying published migration history.

## 2. Verify

- [x] 2.1 Verify CLI/drift and affected migrations, roles/users/permissions/CSRF contracts and PostgreSQL selection.
- [x] 2.2 Run affected static and strict spec checks, preserving unchanged proof.
- [x] 2.3 Obtain independent Medium review, sync and archive the locally verified change before normal publication.

Locally verified audit candidatefe25cf5c passed84 affected cases after the earlier37 migration/role/user and116 permission/session/CSRF checks. Published migration blobs are unchanged. Static checks, strict specs and independent Medium review passed. Five PostgreSQL test selections were added; hosted execution remains a publication gate.
