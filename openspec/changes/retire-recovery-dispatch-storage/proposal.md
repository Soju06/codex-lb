> **Partially reverted by `restore-recovery-dispatch-claim`.** The owner decided
> on 2026-09-11 that `relocate-anchored-turns-across-accounts` (#2374) is the
> caller this proposal proves does not exist, so the durable claim, its refund
> branches, the `expected_recovery_dispatch_count` CAS parameter surface, the
> `recovery_dispatch_count` ORM mapping and the *Fenced one-shot recovery
> dispatch* requirement are restored. What survives from this change is the
> deletion of `HTTPRequestState.operation_attempt_generation` and its hops,
> which carried a constant `0` whether or not the fence has a caller, plus the
> per-writer proof in `design.md` that explains why.

## Why

`drop-bridge-recovery-modes` (#2336) deleted every request-path caller of the
server-owned recovery dispatch and deferred the storage cleanup to a follow-up
that retires it "together with the `recovery_dispatch_count` column
semantics". This is that follow-up.

Every writer of `http_bridge_operations.recovery_dispatch_count` on `main`
sits inside that callerless surface, so no supported path can advance the
counter and the three `expected_recovery_dispatch_count` CAS predicates can no
longer reject anything. A fence that can never reject is not a fence.
`design.md` carries the per-writer proof.

## What Changes

- **Delete the callerless recovery-dispatch surface.**
  `claim_unknown_operation_for_recovery` (repository + coordinator), the
  `max_recovery_dispatches` bound, and the `restore_recovery_dispatch_claim`
  flag on `mark_operation_unknown` (repository + coordinator).
  `mark_operation_unknown` itself stays: it is the live ambiguous-dispatch
  fence.
- **Drop the tautological CAS arguments, keep every fence that still guards
  something.** `expected_recovery_dispatch_count` is removed from
  `append_terminal_operation_event`, `append_terminal_operation_chunk`,
  `settle_terminal_append_failure`,
  `_lock_operation_for_chunk_append` (repository), their coordinator
  pass-throughs, `HttpBridgeOperationEventBatcher.append_terminal_event` /
  `settle_terminal_event`, and the two `upstream_events` call sites. What
  remains:
  - `append_terminal_operation_event`: session-owner instance/epoch fence,
    `session_id`, the `abandoned` refusal, and the
    `spool_format == rows_v1` predicate in the same `SELECT ... FOR UPDATE`.
  - `_lock_operation_for_chunk_append`: session-owner instance/epoch fence,
    `session_id`, and the `abandoned` refusal (the only other caller already
    passed no expected count).
  - `settle_terminal_append_failure`: session-owner fence, `state != abandoned`,
    and the operation-state plus persisted-response-identity `or_` predicate.
    That state predicate is what rejects the delayed fallback settlement of a
    prior attempt after a newer attempt rebinds the row to `submitted`
    (`record_operation` is the only remaining path that does so) — `submitted`
    matches neither `acknowledged` nor the terminal state being settled, so the
    UPDATE touches no row.
- **Delete the now-unread request-state field.**
  `HTTPRequestState.operation_attempt_generation` and its propagation through
  the account-neutral replay and the server-owned retry hand-off in
  `_service/http_bridge/streaming.py`.
- **Keep the physical column this release; retire only the ORM mapping.**
  `HttpBridgeOperationRecord.recovery_dispatch_count` is removed from the
  model and the column is added to `_LEGACY_EXTRA_COLUMNS` in
  `app/db/migrate.py`, exactly as `retire-prewarm-canary-column-mappings`
  (#2189) did for the prewarm canary columns. The Helm migration Job is a
  `pre-upgrade` hook that runs before the old replicas drain, so a
  previous-release replica must still be able to INSERT an operation row. The
  column is `NOT NULL` with `server_default 0`, so this release's INSERTs
  simply omit it and the default fills it. The Alembic drop revision plus the
  allow-list removal is queued in
  `openspec/specs/deployment-installation/context.md` under the next-release
  entry titled "Drop the retired bridge recovery-dispatch column".

No behaviour change for legacy rows either. On `main` the expectation is read
from the row itself (`request_state.operation_attempt_generation =
getattr(operation, "recovery_dispatch_count", 0)` in `request_submit.py`), and
`record_operation`'s `failed` -> `submitted` rebind leaves the counter alone,
so a pre-#2336 row with a non-zero counter always compared equal too. The
predicate was satisfied by every row the code could reach, which is exactly
why removing it is a no-op.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: the durable one-shot recovery-dispatch budget is
  gone with its dispatcher; terminal-append fallback settlement is fenced by
  operation state and persisted response identity rather than by a durable
  attempt generation; the operation ORM no longer maps the retired counter
  column while the physical column remains insertable for legacy replicas.

## Impact

`app/db/models.py`, `app/db/migrate.py`,
`app/modules/proxy/durable_bridge_repository.py`,
`app/modules/proxy/durable_bridge_coordinator.py`,
`app/modules/proxy/http_bridge_event_batcher.py`,
`app/modules/proxy/_service/support.py`,
`app/modules/proxy/_service/http_bridge/{request_submit,streaming,upstream_events}.py`,
and their tests. No Alembic revision and no runtime behaviour change at the
shipped default: every removed predicate was satisfied by every row the
current release can write. No rolling-upgrade caveat — a previous-release
replica still finds the column it maps, and this release never writes it.

## Orphan sweep from #2336

Also checked, and deliberately **not** removed (all still live on `main`):

- `HttpBridgeRecoveryAttemptRecord` and the `recovery_attempt_*` request-state
  fields — the *fresh-replay* recovery journal, a separate table with live
  readers/writers in `_service/http_bridge/upstream_events.py`,
  `request_submit.py` and the shutdown drain in `app/main.py`.
- `request_state.replay_count` / `clean_close_replay_count` — client-observed
  replay accounting, read at 20+ sites.
- `operation_replay`, `operation_created`, `operation_rebound*`,
  `operation_dispatched`, `operation_rebind_required`,
  `operation_persisted_response_id` — all still read on the request path.
- `mark_operation_unknown` and `rollback_operation_before_dispatch` — live
  cleanup primitives.
