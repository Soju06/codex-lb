## Context

Reference commit: `origin/main` 0c8d9219. Verified surfaces:
`AccountsRepository.delete()` and its fold-state lock comment,
`app/modules/accounts/usage_rollup.py` (lifetime fold, `lock_fold_state`),
`app/modules/accounts/usage_time_rollup.py` (hourly/demand/error/conversation
folds, lifecycle mirrors, history-rewrite discipline), `app/core/retention/`
(chunked prune precedent, BATCH_SIZE=10k), duplicate-account consolidation
(`_reconcile_chatgpt_identity_duplicates`, same fold lock), scheduler + leader
election pattern, Alembic single head `20260812_120000_add_sticky_abandonment_scope`.

Production measurements (10.0.0.113): account deletion = single transaction
holding the fold-state lock for the whole drain; ~93k `usage_history` rows ≈
11.6 s each for two accounts; ~133k `request_logs` soft-detach = 313 s (18
indexes ≈ 8.3 GB, every row non-HOT); fold blocked; 73 pool timeouts in 36 h;
HTTP client timeout on the DELETE call.

## Goals / Non-Goals

**Goals:**

- DELETE API returns in milliseconds; the account is immediately invisible to
  listings and unroutable.
- Bulk row work proceeds in bounded background transactions (1k rows each,
  batch selection pinned to account-leading indexes) so the fold, the pool,
  and vacuum are never blocked for minutes.
- Same end state as the synchronous delete for both `delete_history`
  variants, including the folded-bucket lifecycle mirrors and the
  "rollup row deleted with the account row" invariant.
- Restart-safe resume, idempotent repeat requests, explicit supersede path.

**Non-Goals:**

- No change to duplicate-account consolidation (still synchronous under the
  fold lock; its row volume is bounded by the duplicate's history and it must
  stay atomic with the identity swap).
- No change to retention, fold cadence, or watermark semantics.
- No new settings; no dashboard UI for drain progress (the account is simply
  gone; the worker logs outcomes).

## Decisions

### D1: Terminal mark = existing `DEACTIVATED` status + marker columns, not a new enum value

Serving-path exclusion lists are written as denylists
(`status not in (REAUTH_REQUIRED, DEACTIVATED, PAUSED)` in
`proxy/helpers.py`, `load_balancer.py`, `account_cache.py`, `proxy/api.py`,
`realtime_live.py`): a new `DELETING` enum value would be *routable* until
every denylist was found and extended, and would require a PostgreSQL enum
migration. Reusing `DEACTIVATED` inherits every existing exclusion (sticky
purge, bridge close, selection caches) with zero new status handling. The
pending-deletion state itself lives in `accounts.delete_requested_at`
(authoritative marker + queue ordering) and `delete_history_requested`
(variant, frozen at request time); `deactivation_reason="pending_deletion"`
is operator-facing only.

The marker also fences ordinary status writers (`update_status`,
`update_status_if_current` gain `delete_requested_at IS NULL`): a stale
in-flight settlement — e.g. a 429 for a request selected before the DELETE —
would otherwise replace `DEACTIVATED` with `RATE_LIMITED` and make the
account selectable again for direct-by-id paths mid-drain. Credential
replacement does not go through these writers (it writes fields directly and
clears the marker in the same transaction), so the supersede path is
unaffected. Pre-upgrade replicas' writers are unfenced during a rolling
deploy, so every drain chunk additionally self-heals: when the marked row
drifted (non-terminal status or a recreated API-key assignment) without a
credential replacement, the chunk re-asserts the terminal status and
re-removes the assignments under the row lock it already holds — a DB
trigger was rejected as disproportionate machinery for a drift window that
is already bounded to one chunk transaction (seconds).

Repeat DELETE requests short-circuit on an unlocked marker+wipe read before
entering the writer section / row lock: a drain chunk holds the account row
(and, on SQLite, the writer section) for seconds at a time, and the
fast-path contract must hold throughout the drain. The short-circuit falls
through to the full path when the credentials were replaced without
clearing the marker, so an explicit re-delete after a legacy replacement
re-wipes and re-arms the deletion.

`begin_delete` additionally produces the two projections the synchronous
delete's row removal produced instantly: it deletes the account's
`ApiKeyAccountAssignment` rows (key listings and pooled-usage reads exclude
the account immediately; the key's persisted `account_assignment_scope_enabled`
flag keeps the key scoped, exactly as after the FK cascade) and overwrites
the access/refresh/id token ciphertext with empty-credential ciphertext.
The wipe is what keeps the rolling upgrade honest: a pre-upgrade replica's
export endpoints read the row without knowing the marker, and must not be
able to hand out usable credentials during the drain window. Token rotation
is CAS-guarded on the pre-wipe refresh ciphertext (a stale rotation misses),
and every supersede path writes complete fresh ciphertext. The wipe must
not break the reauth supersede path itself: targeted reauthentication
verifies the seat against `chatgpt_user_id` or — on legacy rows where it
was never backfilled — the stored id-token claims, so `begin_delete`
backfills `chatgpt_user_id` from those claims (non-secret identity) in the
same transaction before destroying them.

The wipe doubles as the supersede signal for pre-upgrade replicas: a
replacement handled by old code writes fresh ciphertext but cannot clear
marker columns its ORM does not know. Every marker re-check (chunk and
finalization) therefore also inspects ALL THREE token ciphertexts —
non-wiped or undecryptable material in any field of a marked row means a
replacement happened (a legal replacement may carry an empty refresh token
while providing fresh access/id material, so a refresh-only check would
finalize a freshly replaced account), and the worker clears the marker
itself (under the row lock) instead of draining further or finalizing. API-key assignment validation
(`ApiKeysRepository.list_accounts_by_ids`) likewise rejects marked
accounts, and `replace_account_assignments` locks the target account rows (`FOR SHARE`
on PostgreSQL, which conflicts with `begin_delete`'s row update) BEFORE
touching any assignment row — the same account-then-assignment order
`begin_delete` uses, so the race serializes instead of deadlocking — and
then inserts through a conditional `INSERT … SELECT … WHERE
delete_requested_at IS NULL`. A key create/update whose validation raced
the DELETE therefore cannot recreate an assignment that would re-surface
the account in key listings: either it commits first and `begin_delete`'s
assignment cleanup removes its rows, or the marker is visible to the
insert and the account is skipped.

### D2: The account row is the queue (no new table)

The marker columns make the `accounts` row its own durable work item: the
worker scans `delete_requested_at IS NOT NULL`, progress is the shrinking
`WHERE account_id = :id` predicates, and finalization's row delete is the
dequeue. Restart resume and idempotency need no extra state machine; a
crash between any two chunk transactions loses nothing.

### D3: Chunks do NOT take the fold-state lock; only finalization does

The single-transaction delete held the fold lock to prevent an in-flight
fold slice from committing pre-delete attribution after the mirrors ran
(resurrecting folded rows). The chunked drain preserves that invariant with
lock-free chunks because:

1. Chunk transactions touch only raw rows; they never write a rollup table
   or move a watermark (`usage_history` tables are not fold-governed at all).
2. An interleaved fold slice aggregates either still-attached rows (folded
   under the account dimension) or already-detached rows (folded under the
   orphaned-deleted dimension — the soft-path end state). Both converge at
   finalization: it takes `lock_fold_state()`, detaches/deletes residual raw
   rows, and runs the lifecycle mirrors, which move or remove EVERY folded
   row carrying the account dimension — including rows folded mid-drain.
3. Every fold slice holds the fold-state row lock (`FOR UPDATE` on the
   `account_usage_rollup_state` row) from before it reads raw rows until its
   commit. A slice therefore commits strictly before finalization (its
   output is mirrored) or strictly after (it sees no attributed raw rows).
   Post-finalization resurrection is impossible.

Per-chunk fold-lock acquisition was considered and rejected: it adds fold
stalls proportional to drain length while providing nothing the finalization
lock does not already guarantee (the mirrors are a pure dimension move over
whatever is folded at mirror time).

The letter of the history-rewrite discipline in `usage_time_rollup.py`
("mutations of folded dimensions below the watermark take the fold lock and
mirror or skip **in the same transaction**") is relaxed for this one path:
mid-drain, folded buckets may still attribute to an account whose raw rows a
chunk already detached. That intermediate state never double- or
under-counts a read (folded side serves below-watermark, raw tail above;
the watermark folds each raw row exactly once, and already-folded rows are
never re-read), and on the deletion path it is bounded by drain duration:
the end state is byte-identical to the synchronous path, with the module
docstring of `deletion.py` documenting this as the single sanctioned
exception, converged by finalization.

When a supersede lands after a partial drain, finalization never runs and
the divergence for rows drained before the supersede is the PERMANENT,
intended end state: folded buckets keep attributing that traffic to the
revived account (it is the account's true pre-delete history — nothing is
added or inflated), while the raw rows stay detached (soft) or deleted
(`delete_history`), exactly as the "rows already drained stay detached"
trade-off promises. Reads stay consistent for the same reason as mid-drain:
below-watermark reads are folded-only, drained rows below the watermark are
never re-folded, and drained rows above the watermark fold once under the
orphaned dimension. Reconciling instead (running the lifecycle mirrors at
supersede time) was rejected: it would drag the fold lock and per-row delta
mirroring into every credential-replacement path to "fix" attribution that
is already historically correct.

### D4: Finalization reuses `AccountsRepository.delete()` with a marker guard

`delete(only_pending=True)` is the historical transaction verbatim —
identity-membership lock (PostgreSQL), fold-state lock, residual
usage-history delete, residual detach/delete + mirrors, sticky + rollup +
account row — plus: it aborts (touching nothing) unless the marker is still
set, and it reads the `delete_history` variant from the persisted flag
rather than the caller. The identity-membership `FOR NO KEY UPDATE` row lock
(PostgreSQL) keeps the marker stable through the transaction; on SQLite the
writer section serializes writers. Lock order (identity → fold) matches
consolidation, so no new deadlock ordering is introduced. Residual rows also
cover stragglers: a stream that started before the mark settles its
request-log row at stream end, possibly after every chunk ran.

After the fold lock, finalization upgrades the account row to a full
`FOR UPDATE` lock (PostgreSQL) before the residual sweeps. `FOR UPDATE`
conflicts with the `KEY SHARE` a request-log FK insert takes, so an
in-flight stream's insert either commits before the sweep (and is swept) or
blocks until the transaction commits and then fails its FK against the
deleted row — the same outcome a post-delete insert always had. Without the
upgrade, an insert could commit between the sweep and the account-row
delete, where `ON DELETE SET NULL` would leave a live (`deleted_at IS
NULL`) orphan on the soft path or surviving raw history under
`delete_history`. The lock order (identity → fold → row exclusive) matches
the historical transaction, whose final `DELETE` acquired the same
exclusive lock after the fold lock.

### D5: Supersede-by-replacement, first-request-wins idempotency

`_apply_account_updates` (every credential replacement: re-import, reauth,
slot reuse) clears the marker: account ids are deterministic, so
delete-then-reimport lands on the marked row, and letting the worker delete
a just-reimported account would be data loss. Every chunk transaction and
finalization re-read the marker under the account row lock (PostgreSQL
`FOR NO KEY UPDATE`, compatible with the `KEY SHARE` taken by concurrent
rollup FK inserts; on SQLite the writer section serializes writers), so a
replacement either commits before the marker read (the chunk sees the
cleared marker and stops) or blocks until the chunk commits — no chunk can
mutate rows after a replacement has successfully returned, and a superseded
account is never finalized (rows already drained stay detached — history
loss was requested by the earlier delete). The chunk takes only the account
row lock and touches only that account's child rows, so no new lock ordering
is introduced. Marked accounts are also absent from the credential-export
endpoints: the synchronous delete made exports 404 immediately, and the
asynchronous drain window must not keep decrypted tokens retrievable after
a successful DELETE. Repeat DELETE requests return
success without escalating `delete_history` (first request wins), matching
the synchronous world where a second DELETE arrived after the account was
already gone. `reactivate_account` treats a marked account as not found
rather than racing the worker back to ACTIVE.

### D6: Worker = leader-gated 30 s tick + local wake, cheap pre-check, round-robin pass

Same scheduler shape as retention. Each tick runs one `LIMIT 1` existence
probe *before* leader election — served by the partial index
`idx_accounts_delete_requested_at` (`WHERE delete_requested_at IS NOT
NULL`), which is empty in the steady state — so a tick with nothing to do
costs one tiny index probe. `delete_account` wakes the local worker after
commit: on the leader (the single-replica common case) draining starts
immediately; a follower's wake is a no-op and the leader's tick picks the
request up within 30 s. Batch size 1k: measured ~1.2 s/10k `usage_history`
deletes and ~23 s/10k `request_logs` detaches (18 indexes, non-HOT updates)
bound the worst table at ~2.3 s per transaction — and every chunk holds the
account row lock (`FOR NO KEY UPDATE`) for its full duration, so a supersede
or fenced settlement waits for at most one chunk. Between row-touching
rounds the pass sleeps a fraction of the round's own duration (capped), so
a multi-hundred-chunk drain leaves the 2-vCPU database headroom instead of
running chunk transactions back-to-back.

Chunk batch selection is planner-pinned to the account-leading indexes
(`idx_usage_account_time`, `ix_additional_usage_distinct_labels`,
`idx_logs_account_kind_deleted_latest` — the last one covering, so the
request-log batch is an index-only scan): the batch subquery selects with an
`account_id >= :id AND account_id <= :id` range (equivalent rows, but the
range keeps `account_id` out of the constant-equivalence class and thus in
the sort pathkeys) ordered by the target index's exact column order, making
that index the only sort-free plan. A plain `account_id = :id LIMIT n` shape
was verified on the production planner to run as a LIMIT-terminated Seq
Scan for exactly the large accounts this change targets — with an unbounded
dead-prefix re-scan mid-drain and a guaranteed full heap scan for every
empty probe once per-account statistics go stale. With the pinned shape,
chunk scan work is bounded by the account's own remaining rows and a
drained-table probe is a single index descent. Within one pass, a table
observed empty is not re-probed on later rounds (rows settling mid-drain
are converged by finalization's residual sweep anyway).

A deletion pass round-robins: each round advances every pending account by
at most one NONEMPTY chunk transaction (a round stops at the first chunk
that touched rows, not the first batch-size-full one, so small tables cannot
stack several row-touching transactions into one round) and the pending set
is re-scanned between rounds. A multi-minute drain (the measured 133k-row
account is ~133 chunks) therefore cannot starve another marked account, and
a DELETE that lands mid-pass is picked up by the next round's re-scan rather
than waiting for the whole pass to finish.

### D7: API contract unchanged (`{"status": "deleted"}`)

The dashboard's delete mutation only toasts and refetches the listing, which
already excludes the marked account — the operator-visible contract ("after
DELETE, the account is gone from the list") holds exactly. Returning a new
`"deleting"` status would break any consumer comparing against "deleted"
while conveying nothing actionable: the deletion is irrevocable (modulo
re-import) once the API returns. The spec states row purge is asynchronous.

## Risks / Trade-offs

- **Mid-drain visibility**: statistics pages may briefly attribute folded
  history to the (invisible) account while raw rows are already detached.
  Bounded by drain duration; strictly better than the previous minutes-long
  fold outage.
- **Interleaved folds vs hard delete**: a fold slice between hard-delete
  chunks may fold rows (account and API-key aggregates) that the
  single-transaction path would have deleted first. This is inherent fold
  timing (a fold 1 s before the DELETE captured them under the old code
  too); the account side is removed by the mirrors, and API-key folded sums
  keeping settled traffic is the documented behavior for folded history.
- **Supersede after partial drain**: a re-import that lands mid-drain keeps
  the account but its already-detached rows stay detached (and
  already-deleted rows stay deleted), while folded buckets keep attributing
  the pre-supersede-drained traffic to the revived account — permanently,
  since finalization's mirrors never run. This is historically correct
  attribution (the folded numbers pre-existed the delete), never double- or
  under-counts a read (see D3), and is the documented consequence of the
  operator asking for deletion first.
- **Alembic head races**: the revision sits on the current single head;
  parallel PRs adding revisions require the usual head merge.

## Migration

`20260816_000000_add_account_pending_deletion`: adds
`accounts.delete_requested_at` (nullable DateTime),
`accounts.delete_history_requested` (Boolean, `server_default false`), and
the partial queue index `idx_accounts_delete_requested_at`
(`(delete_requested_at, id) WHERE delete_requested_at IS NOT NULL`), with
existence guards and a symmetric downgrade. Existing rows are
untouched (no pending deletions can predate the feature). Rolling upgrade: an
old replica neither sets nor reads the marker; a delete handled by an old
replica is simply the old synchronous delete, and a delete handled by a new
replica leaves old replicas nothing exploitable — the fast path wipes the
token ciphertext, so old export/read paths that do not know the marker can
only produce empty credentials until finalization removes the row (old
replicas may transiently show the account in listings during the mixed
window; it is unroutable via the terminal status either way).

One mixed-window caveat follows directly from "old replica = old delete": a
repeat DELETE routed to a pre-upgrade replica while the row is still marked
runs the legacy synchronous delete with its caller-provided
`delete_history` variant, which can differ from the frozen first-request
choice (either direction). The legacy delete is still a complete,
fold-locked, mirror-correct deletion — only the history-policy choice
diverges, only inside the deploy window, and only when the operator issues
contradictory repeat requests inside it. Fencing was rejected: new code
cannot retrofit a fence into binaries that predate the marker columns, a
database trigger is disproportionate machinery for the window, and a
"defer background marks until the fleet is upgraded" gate would add a
permanent setting for a transient condition (single-replica deployments —
the production topology — have no mixed window at all).
