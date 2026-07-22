## Context

`UsageUpdater` normalizes one account's `/wham/usage` response into as many as three standard history rows. It currently calls `UsageRepository.add_entry()` once per window. Every call acquires the SQLite writer section and commits independently, so a two-window response performs two durable transactions and a failure can leave only the earlier row visible.

The updater is used with two repository ownership models. Request-scoped callers provide repositories backed by a caller-owned `AsyncSession`; the background scheduler uses an adapter that opens a short-lived background session for each repository operation. The batch operation must preserve both ownership models and must not make independent live-ingest writes wait for a refresh-only API.

## Goals / Non-Goals

**Goals:**

- Persist the normalized primary, secondary, and applicable monthly rows from one account response atomically.
- Acquire the SQLite writer section and commit exactly once for a non-empty standard snapshot.
- Give every standard row in the snapshot one capture timestamp.
- Roll back failed snapshot writes without closing a caller-owned session.
- Let the background adapter own exactly one session for the full snapshot write.

**Non-Goals:**

- Changing which account the scheduler refreshes or how often it refreshes.
- Changing additional per-model usage-history synchronization, identity metadata, or account-status reconciliation.
- Replacing independent single-row persistence used by live usage ingestion.
- Adding a migration, setting, or dashboard/API surface.

## Decisions

### Add a typed account-snapshot repository operation

Introduce a small immutable write model for one normalized standard window and add an `add_account_snapshot` operation to the usage repository contract. `UsageUpdater` will collect every present normalized standard window first and call that operation once.

This keeps transaction ownership in the repository instead of exposing `commit=False` flags or transaction primitives to the updater. Reusing `add_entry()` in a loop was rejected because it preserves the current partial-commit behavior; changing `add_entry()` to stop committing was rejected because live-ingest and other existing callers rely on it as a complete persistence operation.

### Use one timestamp and one explicit rollback boundary

The repository will resolve `recorded_at` once, stage every row on its existing session, acquire one SQLite writer section, and commit once. If staging or commit raises, it will roll back that session before re-raising. The repository will not close the session, so request-scoped callers retain ownership and can continue using it after a handled failure.

The background adapter will open one background session, delegate the whole operation to the session-backed repository, detach returned rows, and then let the background-session context close its own session. Opening a separate session per row was rejected because it cannot provide an atomic snapshot.

### Keep additional usage synchronization separate

Additional per-model limits have independent retention semantics and a separate repository contract. Folding that synchronization into this change would broaden the transaction and require redesigning both repositories. This focused change makes the high-frequency standard rows reported in issue #708 atomic while leaving additional usage behavior unchanged.

## Risks / Trade-offs

- [Risk] A transaction now contains up to three inserts instead of one, holding the SQLite writer section slightly longer. -> The total lock time is lower than repeated lock/commit cycles, and the batch is strictly bounded to the standard windows of one account.
- [Risk] A failure in one window now discards rows that previously might have committed. -> Atomic rollback prevents mixed-time snapshots; the next scheduled refresh retries the complete account.
- [Risk] Test doubles that implement the repository protocol must model the batch operation. -> Keep the DTO and method narrow and update the shared updater stub so existing behavioral tests still assert the emitted rows.

## Migration Plan

No schema or data migration is required. Deploying the code changes only the transaction boundary for new rows. Rolling back restores per-row commits; rows already written by either version remain compatible.

## Open Questions

None.
