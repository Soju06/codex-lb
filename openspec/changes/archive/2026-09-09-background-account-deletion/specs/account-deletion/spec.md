# account-deletion Delta

## ADDED Requirements

### Requirement: Account deletion requests return fast and hide the account immediately

`DELETE /api/accounts/{account_id}` MUST NOT perform the account's bulk row
work (raw request-log detach/delete, usage-history removal) on the request
path. The request MUST only stamp a durable pending-deletion marker in a
short transaction: terminal `DEACTIVATED` status, the pending-deletion
marker (`delete_requested_at`), the frozen `delete_history` choice
(`delete_history_requested`), sticky-session removal, bridge-session
closure, API-key account-assignment removal (the projection the synchronous
delete's FK cascade produced — key listings and pooled-usage reads exclude
the account immediately while the key's persisted assignment-scope flag is
untouched), and an overwrite of the stored access/refresh/id token
ciphertext with empty-credential ciphertext so that NO reader of the
surviving row — including a pre-upgrade replica during a rolling deploy,
whose export endpoints do not know the marker — can produce usable
credentials during the drain window. Repeat DELETE requests MUST
short-circuit before taking the account row lock or the SQLite writer
section, so the millisecond contract holds even while a drain chunk
transaction is holding the row. Because targeted reauthentication (a
supersede path) verifies the seat against `chatgpt_user_id` or, on legacy
rows where it was never backfilled, the stored id-token claims, the fast
path MUST preserve the non-secret seat identity before the wipe by
backfilling `chatgpt_user_id` from those claims when it is absent. The
response contract remains `{"status": "deleted"}` with 200 for an existing
account and 404 otherwise; row purge is asynchronous.

Accounts carrying the pending-deletion marker MUST be excluded from account
listings (`GET /api/accounts` and every listing-derived read) and MUST be
excluded from proxy serving via the terminal status. EVERY ID-based account
surface MUST report a marked account as not found (or absent) — reads
(trends, reset-credit views), mutations (account update, alias,
limit-warmup, routing policy, upstream-proxy binding), action routes
(pause, probe, reset-credit consumption on both the dashboard and
rate-limit route families, `/v1` reset-credit redemption, reactivation),
and the credential-export endpoints (account export, auth export, opencode
auth export) — because the synchronous delete returned 404 on all of them
once the row was removed, and a successful DELETE MUST NOT leave decrypted
tokens retrievable during the background drain window. Only
credential-replacement paths (re-import, reauthentication) may address the
marked row.

Ordinary status writes MUST NOT modify a marked account: a stale in-flight
settlement (for example a 429 landing after the DELETE for a request
selected before it) must not replace the terminal `DEACTIVATED` state and
make the account selectable mid-drain. Only a credential replacement —
which clears the marker — may change a marked account's state. Because
pre-upgrade replicas' status writers are unfenced during a rolling deploy,
every drain chunk transaction MUST re-assert the terminal status (and
re-remove any recreated API-key assignments) under the account row lock
when the marked row has drifted without a credential replacement, bounding
such drift to one chunk transaction; after the repairing chunk commits, the
worker MUST propagate the same cache invalidation as the delete request
(routing unavailability, selection/API-key snapshots, routing-change bump)
so replicas that cached the drift stop serving it.

Marked accounts MUST be rejected by API-key account-assignment validation
and excluded from API-key pooled-usage projections, and assignment
insertion MUST re-check the marker atomically with the write (a conditional
insert; on PostgreSQL additionally serialized against the delete mark by a
`FOR SHARE` lock on the account rows, acquired BEFORE any assignment-row
mutation so the lock order matches the delete path's account-then-assignment
order and the race serializes instead of deadlocking), so an assignment
created or updated after — or racing — the DELETE cannot re-surface the
account in key listings before finalization.

#### Scenario: Delete responds without draining rows

- **GIVEN** an account with raw request-log and usage-history rows
- **WHEN** `DELETE /api/accounts/{id}` returns 200 `{"status": "deleted"}`
- **THEN** the account no longer appears in `GET /api/accounts`
- **AND** the account row still exists, terminal and marked, with its raw
  rows untouched until the background worker drains them

#### Scenario: Marked account cannot be reactivated

- **GIVEN** an account marked for background deletion
- **WHEN** `POST /api/accounts/{id}/reactivate` is called
- **THEN** the response is 404 `account_not_found`

#### Scenario: All ID-based routes report the marked account as gone

- **GIVEN** an account marked for background deletion whose rows are not yet
  drained
- **WHEN** any ID-based account route (trends, reset-credit read/consume,
  probe, pause, update, alias, limit-warmup, routing policy) is called
- **THEN** the response is 404 `account_not_found`

#### Scenario: Marked account no longer serves credential exports

- **GIVEN** an account marked for background deletion whose rows are not yet
  drained
- **WHEN** any credential-export endpoint is called for the account
- **THEN** the response is 404 and no token material is returned
- **AND** the row's stored token ciphertext decrypts to empty credentials
  (nothing usable remains for readers that do not know the marker)

#### Scenario: Seat identity survives the token wipe for reauth supersede

- **GIVEN** a legacy account whose `chatgpt_user_id` is unset (seat identity
  lives only in the stored id-token claims)
- **WHEN** `DELETE /api/accounts/{id}` marks the account and wipes the token
  ciphertext
- **THEN** `chatgpt_user_id` is backfilled from the id-token claims in the
  same transaction, so a targeted reauthentication can still verify the
  seat and supersede the deletion

#### Scenario: Deleted account leaves API-key listings immediately

- **GIVEN** an account assigned to an API key
- **WHEN** `DELETE /api/accounts/{id}` returns
- **THEN** the key's listed assigned-account ids no longer contain the
  account and its pooled-usage projection excludes it
- **AND** the key's assignment-scope flag remains enabled

#### Scenario: Marked account cannot be assigned to an API key

- **GIVEN** an account marked for background deletion
- **WHEN** an API-key create or update names the account in its assigned
  account ids
- **THEN** the request is rejected as referencing an unknown account

#### Scenario: Stale settlement cannot resurrect a marked account

- **GIVEN** an account marked for background deletion
- **WHEN** an ordinary status write (e.g. a late rate-limit settlement)
  targets the account
- **THEN** the write is rejected and the account stays terminal and marked

#### Scenario: Drift written by an unfenced pre-upgrade replica is re-fenced

- **GIVEN** a marked account whose status was replaced (or whose API-key
  assignment was recreated) by a pre-upgrade replica's unfenced writer,
  with the token ciphertext still wiped
- **WHEN** the next drain chunk transaction runs
- **THEN** the terminal status and reason are re-asserted and the recreated
  assignment is removed, in the same chunk transaction

#### Scenario: Repeat delete stays fast during an active drain

- **GIVEN** a marked account whose drain chunk transaction currently holds
  the account row lock
- **WHEN** a repeat `DELETE /api/accounts/{id}` arrives
- **THEN** it returns success without waiting for the chunk transaction

### Requirement: Background worker drains marked accounts in bounded chunks

A leader-gated background worker MUST drain each marked account's
`usage_history`, `additional_usage_history`, and `request_logs` rows in
bounded per-transaction chunks (at most `DELETE_BATCH_SIZE` rows per
transaction) without holding the fold-state lock, and MUST then finalize in
ONE fold-state-locked transaction that detaches or deletes residual raw rows
(including request-log rows settled mid-drain by in-flight streams), runs
the folded-bucket lifecycle mirrors, and removes the sticky, lifetime-rollup,
and account rows together. Finalization MUST serialize against in-flight
raw-row inserts (on PostgreSQL by upgrading the account row to a full lock
that conflicts with the FK's `KEY SHARE` before the residual sweep), so a
log row committed by an in-flight stream is either swept by finalization or
its insert fails against the already-deleted account — finalization may
leave behind neither a live orphan row (soft variant) nor surviving raw
history (`delete_history` variant). The soft variant MUST detach raw rows
(`account_id=NULL, deleted_at` set); the `delete_history` variant MUST
delete them. The worker MUST start a drain promptly after a delete request
on the leader replica and within one worker interval otherwise. A deletion
pass MUST round-robin across pending accounts — at most one nonempty chunk
transaction per account per round — and re-scan for newly marked accounts
between rounds, so one account's long drain cannot delay another marked
account's drain start by more than one chunk transaction per pending
account. Chunk batch selection MUST be served by an account-leading index
on every drain table (on PostgreSQL: `idx_usage_account_time`,
`ix_additional_usage_distinct_labels`, and the covering
`idx_logs_account_kind_deleted_latest`), so per-chunk scan work is bounded
by the account's own remaining rows: it MUST NOT degrade to sequential
scans when per-account statistics are large or stale mid-drain, and a probe
of an already-drained table MUST terminate on the index without scanning
the heap. Within one pass, the worker MUST NOT re-probe a drain table it
already observed empty for an account (rows that land after that
observation are swept by finalization's residual pass), and it MUST pause
between consecutive row-touching rounds in proportion to the round's
duration so a long drain does not run chunk transactions back-to-back.

#### Scenario: Chunked drain reaches the synchronous end state (soft)

- **GIVEN** a marked account whose raw rows exceed one chunk
- **WHEN** the worker completes the drain and finalization
- **THEN** every raw request-log row is detached and soft-deleted, usage
  snapshots are removed, and the sticky, lifetime-rollup, and account rows
  are deleted in the finalization transaction

#### Scenario: Chunked drain reaches the synchronous end state (delete_history)

- **GIVEN** an account marked with the `delete_history` variant
- **WHEN** the worker completes the drain and finalization
- **THEN** the account's raw request-log rows are deleted and its folded
  time-axis buckets are removed

#### Scenario: In-flight log insert cannot escape finalization

- **GIVEN** a marked account whose drain is complete and an in-flight stream
  holding an uncommitted request-log insert for it
- **WHEN** finalization runs
- **THEN** finalization waits for the insert to commit and sweeps the late
  row (or the insert fails against the deleted account), leaving no live
  orphan and no surviving history

#### Scenario: A long drain does not starve other marked accounts

- **GIVEN** one marked account whose drain spans many chunks
- **WHEN** another account is marked for deletion (before or during the pass)
- **THEN** the second account's drain starts within one chunk round and both
  accounts finalize

#### Scenario: Chunk selection stays on the account index

- **GIVEN** a marked account whose per-account row estimate is large (or
  stale after a partial drain)
- **WHEN** a drain chunk selects its batch on PostgreSQL
- **THEN** the batch subquery is planned as a scan of the account-leading
  index on each drain table (index-only for `request_logs`), not a
  sequential scan, and an empty probe terminates on the index

#### Scenario: Drained tables are not re-probed within a pass

- **GIVEN** a marked account whose usage tables drained while its request
  logs still span further chunks
- **WHEN** subsequent rounds of the same pass advance the account
- **THEN** the drained tables' chunk transactions do not run again and
  finalization still sweeps any rows that landed after the empty
  observation

### Requirement: Interleaved fold slices never resurrect a deleted account's folded rows

Fold passes MUST remain able to run between drain chunks. Because every fold
slice holds the fold-state row lock from before reading raw rows until its
commit, and finalization takes the same lock before running the lifecycle
mirrors over whatever is folded at that moment, a fold slice MUST either
commit before finalization (its account-attributed output is moved or
removed by the mirrors) or after (it observes no raw rows attributed to the
account). After finalization commits, no folded row in any rollup table may
carry the deleted account's dimension, and under the soft variant the
orphaned-deleted dimension MUST preserve the account's full folded history.

#### Scenario: Fold between chunks is converged by finalization

- **GIVEN** a marked account with part of its raw history already detached
  by drain chunks and part still attached
- **WHEN** a fold pass commits between chunks (attributing the still-attached
  rows to the account) and the worker then completes finalization
- **THEN** no rollup table contains rows under the account's dimension
- **AND** (soft variant) the orphaned-deleted dimension carries the account's
  complete folded history
- **AND** fold passes run after finalization add nothing under the account's
  dimension

### Requirement: Deletion is restart-safe, idempotent, and superseded by credential replacement

All drain progress MUST live in the database so a worker restart resumes an
interrupted deletion with no separate recovery step. Repeat DELETE requests
for a marked account MUST succeed idempotently and MUST NOT change the
frozen `delete_history` choice (first request wins). The first-request-wins
invariant is scoped to replicas running this revision: during a rolling
deploy, a repeat DELETE routed to a pre-upgrade replica performs the legacy
synchronous delete with its caller-provided variant (exactly the pre-change
behavior — a complete, mirror-correct deletion whose variant choice may
differ from the frozen one). This window is bounded by the deploy itself,
requires the operator to issue contradictory repeat requests inside it, and
is accepted: new code cannot fence binaries that predate the marker, and a
deployment gate would add permanent configuration for a transient window. A credential
replacement (re-import or reauthentication landing on the marked row) MUST
clear the marker and supersede the deletion: every drain chunk and the
finalization transaction MUST re-check the marker under the account row lock
(PostgreSQL `FOR NO KEY UPDATE`; the SQLite writer section) before mutating
rows, so no chunk commits row work after a replacement committed and a
superseded account is never finalized (rows already drained stay detached).

After a supersede that followed a partial drain, rows drained before the
replacement keep their drained end state (detached under the soft variant,
deleted under `delete_history`), and folded rollups keep attributing the
pre-supersede-drained traffic to the revived account: it is the account's
true pre-delete history, and reads MUST NOT double- or under-count as a
result (below-watermark reads are folded-only; drained rows above the
watermark fold exactly once, under the orphaned dimension).

A credential replacement handled by a pre-upgrade replica during a rolling
deploy writes fresh credentials but cannot clear marker columns unknown to
its ORM. The worker MUST therefore treat non-wiped (or undecryptable)
ciphertext in ANY of the access/refresh/id token fields of a marked row as
a credential replacement (a legal replacement may carry an empty refresh
token while providing fresh access/id material): it MUST clear the marker
itself under the account row lock and abandon the deletion without mutating
any further rows.

#### Scenario: Restart resumes a partial drain

- **GIVEN** a marked account whose drain was interrupted after some chunks
- **WHEN** a fresh worker pass runs
- **THEN** the drain resumes from the database state and finalizes normally

#### Scenario: Repeat delete does not escalate the variant

- **GIVEN** an account marked by a request without `delete_history`
- **WHEN** a second `DELETE` request arrives with `delete_history=true`
- **THEN** the request succeeds and the frozen choice remains the soft variant

#### Scenario: Re-import supersedes a pending deletion

- **GIVEN** a marked account mid-drain
- **WHEN** a credential replacement lands on the row and clears the marker
- **THEN** the worker abandons the deletion without removing the account row
- **AND** rows detached before the replacement remain detached

#### Scenario: Legacy-replica replacement supersedes without clearing the marker

- **GIVEN** a marked account mid-drain whose credentials were replaced by a
  pre-upgrade replica (fresh ciphertext, marker still set)
- **WHEN** the worker's next chunk or finalization re-checks the row
- **THEN** the worker clears the marker, abandons the deletion, and the
  fresh credentials survive
