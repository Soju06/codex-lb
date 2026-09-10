# Verification

Implementation candidate `b1a4493230ef562ef43f3284a304e641f7b70fa8`, reset-only base `99473ed1138e2e3c72ca855528a0d662bfeb60e3`. The archive follow-up adds the docs navigation entry and clarifies the disabled-policy scenario. It does not change runtime behavior.

## Completeness and correctness

All five tasks are complete. All six added or modified requirements have implementation and regression evidence.

| Requirement | Evidence |
| --- | --- |
| Operator controls pool redemption | Default-off settings migration, dashboard write authorization, settings component tests, disabled and unauthorized route tests |
| Genuine available inventory | Projection expiry/count/duplicate checks, fresh store observations, bounded refresh, cancellation and partial-failure route tests |
| Earliest-expiry default | Native route tests for earliest owner, explicit selection, expired contributions and post-consume counts |
| Stable retries | Database first-writer test, lost response and replica retry tests, cache loss, short-ledger expiry, deletion, reauthorization, policy change and conflict tests |
| Account-owned quota metadata | Existing Desktop quota tests plus enabled cached summary and disabled original envelope tests |
| Relay passthrough | Exact list/consume methods and aliases, unchanged body/query/identity, wrong-method passthrough and outbound proxy isolation tests |

## Validation

The affected Python suite passed 271 tests. Two PostgreSQL-only migration cases were skipped there and executed separately. PostgreSQL passed 39 reset route, race and migration tests, followed by five final migration/legacy-remap cases. Databases and upstream responses were synthetic; the disposable PostgreSQL container was removed.

The broader unit/simulation run passed 9,182 tests with 99 skips and one expected failure. It found two legacy revision-remap failures caused by reapplying the new migration. The migration now checks existing columns and table, following adjacent migrations. All 60 affected SQLite migration tests pass after the fix. The broad suite was not repeated for this isolated migration fix.

All 1,283 frontend tests pass. Frontend lint, typing and production build pass. Python lint, formatting, full typing and architecture checks pass. All 66 OpenSpec specs and the change pass strict validation. The docs build initially found the missing navigation entry; adding that entry makes `mkdocs build --strict` pass.

Before/after settings screenshots and a 390px mobile screenshot were reviewed. The switch uses the existing card component and fits without horizontal overflow. The UI detector reported no findings.

## Review and delivery boundary

Independent review found native idempotency-key rewriting and dependence on the helper's expiring ledger. Both were fixed and independently retested. Additional route coverage verifies that reauthorization cannot change a pinned upstream owner and conflicts return HTTP 409 in the native error envelope.

Non-draft PR #2289 closes issue #2288 and declares its dependency on #2286. Initial current-head GitHub inspection found a mergeable PR, checks queued or running, and no review threads. Hosted success is not established by these local results; current status remains on the PR.

Installed-client request inspection covers Desktop 26.903.61454 with CLI 0.153.4. No real credit was consumed. The pooled-reset feature is not deployed, and live native acceptance remains outstanding. The app-server reset RPC remains original-account scoped.
