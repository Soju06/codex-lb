# Design

## Proof: no writer can advance `recovery_dispatch_count` on `main`

Every occurrence of the column name in the tree at `origin/main`, grouped by
role (`grep -rn recovery_dispatch_count .`, excluding `.git`/`node_modules`).

Writes — all three sit inside the callerless surface this change deletes:

- `durable_bridge_repository.py` `operation.recovery_dispatch_count += 1`, the
  only increment, inside `claim_unknown_operation_for_recovery`. Its only
  in-repo references are the `DurableBridgeSessionCoordinator` pass-through
  (itself callerless) and repository tests; the request-path caller went away
  with `drop-bridge-recovery-modes`.
- Two `-= 1` refunds in `mark_operation_unknown`, both guarded by
  `restore_recovery_dispatch_claim and recovery_dispatch_count > 0`. The
  single production caller
  (`request_submit._submit_http_bridge_request_with_handoff`) passes only
  `operation_id`/`session_id`/`instance_id`/`owner_epoch`, so the flag is
  always `False`; and with no increment left, the `> 0` floor could never hold
  either.

Non-writes: the ORM mapping (retired here), the Alembic revision that created
the column `NOT NULL DEFAULT 0` plus its merge revision, the read-only
`DurableBridgeOperationSnapshot` field and its row mapper, the three
`== expected_recovery_dispatch_count` read predicates, the
`request_state.operation_attempt_generation` initialiser in
`request_submit.py`, keyword pass-throughs in `durable_bridge_coordinator.py`
/ `http_bridge_event_batcher.py` / `_service/http_bridge/upstream_events.py`,
and tests/prose. No raw `text()` SQL, no bulk `update(...).values(...)` and no
ORM INSERT anywhere in `app/` names the column; the only INSERT that still
sets it is a deliberately legacy-shaped one in
`tests/integration/test_migrations.py`.

## Why the CAS predicates are safe to drop

The expectation was never a hardcoded constant: `request_submit.py` seeds
`request_state.operation_attempt_generation` from the snapshot's
`recovery_dispatch_count`, and `streaming.py` only preserves or copies that
value across the account-neutral replay and the server-owned retry hand-off.
Because no writer can move the column between the snapshot and the append, and
because `record_operation`'s `failed` -> `submitted` rebind leaves the counter
alone, the predicate compared a value to itself for every row the code could
reach — including rows carried over from before `drop-bridge-recovery-modes`
with a non-zero counter. Removing it is a no-op, not a latent fix.

Note that `append_terminal_operation_event` and `_lock_operation_for_chunk_append`
therefore had no newer-attempt discriminator before this change either: the
counter did not move across a rebind, so the surviving fences (session owner
instance/epoch, `session_id`, the `abandoned` refusal, and `spool_format ==
rows_v1` for the terminal append) are the same protection they always were.
`settle_terminal_append_failure` keeps its real newer-attempt fence in the
operation-state plus persisted-response-identity `or_` predicate.

## Column retention

`retire-prewarm-canary-column-mappings` (#2189) is the precedent: retire the
ORM mapping now, allow-list the physical column in `_LEGACY_EXTRA_COLUMNS` so
the schema-drift gate stays clean, and queue the Alembic drop for the release
after the last release whose ORM mapped it. The Helm migration Job is a
`pre-upgrade` hook that runs before the old replicas drain, so a
previous-release replica must still be able to INSERT an operation row that
names the column. The column is `NOT NULL` with `server_default 0`, so this
release's INSERTs simply omit it.
