## 1. Deterministic regression coverage

- [x] 1.1 Add a no-sleep integration test that queues a primary/secondary live
  snapshot for duplicate `D` with local and upstream identities, completes
  same-slot reconciliation into canonical `C`, then directly consumes the
  captured item.
- [x] 1.2 Assert exactly one persisted row for each represented window under
  `C`, no usage row under `D`, no duplicate snapshot, and preservation of the
  injected usage/reset/credits values.
- [x] 1.3 Add controls proving a still-valid local id is preferred even when an
  upstream fallback exists, and an upstream-only queued item still resolves to
  its unique local account.
- [x] 1.4 Capture the focused failing-first command and RED output before any
  production edit; do not use sleeps, polling delays, retries, or a background
  consumer timing race.
- [x] 1.5 Add a deterministic two-session PostgreSQL regression that pauses at
  exact transaction events and proves consolidation cannot reparent before a
  snapshot append and then cascade-delete that append.
- [x] 1.6 Add both legal PostgreSQL interleavings for queued identity `X` when
  the selected local owner currently belongs to `Y`, including the causal RED
  where `Y` reconciliation wins the owner row and deletes it before lookup.

## 2. Publication ownership envelope

- [x] 2.1 Retain both local account id and upstream ChatGPT account id in the
  typed live-usage hub/queue contract.
- [x] 2.2 Update every local-account HTTP/SSE and WebSocket publication tap
  point to supply the upstream identity when available, preserving the existing
  upstream-only path and no-op hub behavior.

## 3. Consume-time settlement

- [x] 3.1 Resolve the persistence owner in the background ingestion session:
  prefer an existing local row; if it is stale or absent, accept only one
  current row matching the captured upstream identity.
- [x] 3.2 Protect owner resolution through persistence and write all represented
  windows atomically so the item settles once under one account on SQLite and
  PostgreSQL; use one shared transaction-scoped upstream-identity lock across
  settlement, ordinary/slot upserts, replacement, rotation, metadata update,
  consolidation, and deletion before row/fold locks.
- [x] 3.3 Keep ambiguous/missing ownership serving-safe and logged; do not alter
  account consolidation policy, queue overflow, throttling, or retry behavior.
- [x] 3.4 Add no Alembic revision, model column, setting, or API schema change.
- [x] 3.5 Roll back before bounded relock of the canonical captured/current
  identity set, reselect and revalidate ownership, and raise a typed terminal
  error on a second identity change without fabricating a null lock key.

## 4. Automated verification

- [x] 4.1 Run the focused live-ingestion integration selection once to GREEN,
  proving stale-local consolidation, valid-local preference, and upstream-only
  resolution.
- [x] 4.2 Run diagnostics on every changed Python file and the affected backend
  lint/type/test gates on both supported database paths where registered.
- [x] 4.3 Run `openspec validate settle-live-usage-after-account-consolidation --strict`.
- [x] 4.4 Run the deterministic PostgreSQL race repeatedly plus lock-routing,
  identity, live-ingest, snapshot, and HTTP publication regressions after the
  shared lock implementation is complete.
- [x] 4.5 Run the selected-owner identity race in both transaction orders and
  the focused no-relock, one-relock, terminal-change, rollback, sorted-lock,
  and null-identity unit coverage.

## 5. Authenticated QA and cleanup

- [x] 5.1 Start an isolated QA database/backend, reproduce `D -> C` settlement,
  and execute authenticated `curl -i GET /api/accounts` with the QA bearer key.
- [x] 5.2 Capture HTTP 200 evidence showing exactly one canonical `C`, no `D`,
  and the injected primary and secondary usage values; capture an independent
  database diff showing one canonical row per represented window and no
  duplicate-owned row.
- [x] 5.3 Stop and remove every QA process, listener, credential, database file,
  and temporary artifact; record the cleanup receipt.
