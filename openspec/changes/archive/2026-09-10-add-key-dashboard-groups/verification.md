# Verification: add-key-dashboard-groups

## Requirement coverage

| Requirement | Evidence |
| --- | --- |
| Administrator-managed groups | API CRUD normalization, omission, clearing, regeneration and key-holder write denial in `tests/integration/test_key_dashboard_groups.py`; create/edit form submissions in frontend component tests. |
| Private thirty-day usage | API tests assert response field allowlists, masked prefixes, case-sensitive group isolation, forbidden selectors, start-inclusive/end-exclusive bounds, unused members, expired/inactive authentication, and current persisted membership. |
| Retained historical totals | API test compares raw totals, folded totals, and totals after raw pruning, including deleted-account usage, missing input tokens, excessive cache counts, and negative cache counts. |
| Accessible Group keys tab | `key-dashboard-flow.test.tsx` covers lazy loading, keyboard focus, group totals, refresh/removal, empty state, retry, remembered-key 401 cleanup, and late responses after unmount/disconnect. |
| Migration compatibility | `test_key_group_migration.py` upgrades an existing key, checks schema drift, downgrades while preserving the key, and upgrades again. |

## Completed checks

- Key-dashboard API: 10 passed.
- Group API: 9 passed.
- Migration upgrade/downgrade/re-upgrade: 1 passed.
- API-key service unit tests: 87 passed.
- Focused API-key CRUD/update/regeneration integration: 28 passed, 78 unrelated cases deselected.
- Frontend key-dashboard integration: 13 passed.
- Frontend create/edit forms, API-key schemas, and localization: 62 passed.
- Python `ty check`, targeted Ruff checks/format, frontend typecheck and ESLint: passed.
- Frontend production build: passed.
- Isolated SQLite `codex-lb-db upgrade head` followed by `check`: migration policy OK, no schema drift.
- Strict validation of this change and all 59 main specs: passed.

## Visual evidence

- Before: [key dashboard](../../../../docs/screenshots/key-groups-before.png).
- After: [desktop group tab](../../../../docs/screenshots/key-groups-after.png).
- After: [390px mobile group tab](../../../../docs/screenshots/key-groups-mobile.png).
- Light and dark mobile views inspected; no page-level horizontal overflow. The member table scrolls within its container.

## Coherence and limits

All three added requirements follow the design: one nullable indexed membership field, existing admin authorization, persisted membership lookup, allowlisted DTOs, sequential reads on one request session, and abortable tab-local fetching. Main spec and context are synchronized. No new setting, main navigation item, package dependency, or shared quota was added.

The exact rolling window follows existing hourly-rollup retention semantics: a pruned partial-hour boundary cannot be reconstructed exactly. Group names opt current members into viewing the entire recent window, including usage before joining.

The broad API-key suite was interrupted by SIGTERM (exit 143) before completion without reporting a test failure; it is not counted as a completed run. Focused API-key CRUD coverage is recorded separately below. PostgreSQL execution and production deployment were not performed.

## Final assessment

All scoped implementation and verification tasks are complete. No unresolved requirement or design mismatch was found. Ready to archive; no production deployment was requested.
