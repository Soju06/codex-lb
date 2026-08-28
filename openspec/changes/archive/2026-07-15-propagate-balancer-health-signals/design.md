# Design: propagate-balancer-health-signals

## Decisions

### 1. Persist the cooldown in existing columns — no migration

The cooldown deadline is written into `accounts.reset_at` (INTEGER epoch) alongside the
already-persisted `accounts.blocked_at`. `handle_rate_limit` already sets `blocked_at`, and
`mark_rate_limit -> _persist_state -> update_status()` already persists both columns; the only
change is populating `state.reset_at` when upstream metadata is absent.

Precedent: `handle_quota_exceeded` already synthesizes `reset_at = now + 3600` when metadata is
missing, and `background_recovery_state_from_account` plus the usage-refresh reconcile path
already treat persisted `reset_at` as the cooldown source for `RATE_LIMITED` (it seeds
`runtime.cooldown_until = reset_at`), so peers and the scheduler become coherent for free.

Rejected: a dedicated `accounts.cooldown_until` column — needs an Alembic revision, backfill,
and reader changes for a semantic `reset_at` already carries. Rejected: a peer-only min-cooldown
from `blocked_at` without persisting `reset_at` — leaves the row advertising `RATE_LIMITED` with
no recovery deadline and keeps dashboard/reconcile blind to the cooldown. We do keep a
`blocked_at + 30s` peer-side floor as defense-in-depth for legacy rows written before this change.

### 2. `RATE_LIMITED_MIN_COOLDOWN_SECONDS = 30.0` floor, backoff-fallback case only

Retry-After hints persist verbatim (per the existing account-routing requirement). The floor is
needed because `backoff_seconds(1)` is roughly 0.2s — persisting that alone gives peers no
protection. 30s is deliberately below the existing `QUOTA_EXCEEDED_COOLDOWN_SECONDS = 120`
debounce. The marking replica's freshness gate (`cooldown_ready` + post-block usage row) can
still recover earlier locally, so single-replica recovery latency is bounded by the floor only
when no fresh usage evidence exists.

### 3. No new locks

All writes ride existing single-statement paths: on PostgreSQL, `update_status` /
`update_status_if_current` are atomic conditional UPDATEs under READ COMMITTED (a peer holding a
pre-mark snapshot fails the CAS WHERE clause and cannot clobber); on SQLite the same statements
run inside `sqlite_writer_section()` under the single-writer file lock. The fix works by making
the peer's decision correct (it sees the persisted deadline), not by strengthening the CAS — CAS
already prevents stale-snapshot clobbers; it cannot prevent an intentional-but-wrong transition,
which is why the deadline must be durable.

### 4. Transient signals stay per-replica, now stated normatively

`error_count`/`last_error_at`/error backoff, drain/probe health tiers, probe streaks, and
in-flight/lease pressure remain per-replica advisory state. Rejected: persisting error counters
or an `account_error_events` table — adds synchronous DB writes on the hot proxy error path, and
per-replica convergence (each replica independently backs off after its own 3 errors) is
acceptable, bounded behavior. Retry-After cooldowns DO propagate — via the `reset_at`
persistence above, since they arrive on the same 429 path.

### 5. Warm-up and presentation interactions (verified)

The limit warm-up "reset confirmed" trigger compares `reset_at` of *usage-history window
entries* before/after refresh (`app/modules/limit_warmup/service.py`), never `accounts.reset_at`,
so a synthetic cooldown deadline cannot trip warm-up. Dashboard/quota presentation shows the
persisted deadline as a near-term reset for metadata-free 429s; precedent exists
(`QUOTA_EXCEEDED` synthesizes `now + 3600`). The `select_account` retry hint is independently
clamped by `SELECTOR_RETRY_HINT_MAX_SECONDS`.

## Scope deviation from the original design document

The authoritative design's `split_recommendation` names two independent follow-ups; per the
one-concern-per-PR gate this change implements only the core (PR1):

- **Implemented**: rate-limit cooldown persistence + peer-side legacy floor + CAS recovery +
  "transient signals are replica-local" normative documentation (account-routing only).
- **Follow-up (not here)**: replica-decorrelated round-robin tie-breaking (keyed-hash final
  tiebreak in `_round_robin_sort_key`).
- **Follow-up (not here)**: stateless staleness-first usage-refresh account selection replacing
  the in-memory `_next_account_index` cursor (usage-refresh-policy).

No other deviations: the implementation matches the design's task list for PR1.
