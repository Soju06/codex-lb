# Context: propagate-balancer-health-signals

## Failure mode being fixed

Two replicas share one database. Account X returns a 429 with no
Retry-After/resets metadata. Replica A marks X `RATE_LIMITED`
(`blocked_at` persisted, `reset_at` NULL, 0.2-120s cooldown only in A's
memory) and stops routing to it. Replica B's very next selection loads X,
computes `effective_runtime_reset = None`, and `apply_usage_quota` flips X
back to `ACTIVE` because usage is below 100%; B CAS-writes `ACTIVE` /
`blocked_at = NULL` and routes the request to X — which 429s again. The row
flaps for the throttle's duration and every replica except A hammers the
throttled account.

## Why 30 seconds

`backoff_seconds(1)` is ~0.2s; persisting it unfloored gives peers no
protection. 30s sits below the `QUOTA_EXCEEDED` 120s debounce and below the
default 60s usage-refresh interval, so a metadata-free 429 holds the account
out of the fleet for at most one refresh cycle before recovery evidence can
land. The marking replica can still recover the account earlier: its runtime
cooldown keeps the raw backoff, and the existing freshness gate (post-block
usage row) clears the persisted deadline through the CAS path.

Operator-visible behavior change on a single replica: a metadata-free 429 now
shows a near-term `reset_at` (about 30s out) on the dashboard and holds the
account out of rotation for up to 30s when no fresh post-block usage evidence
exists. Precedent: `QUOTA_EXCEEDED` already synthesizes `reset_at = now + 3600`
when metadata is missing.

## Interactions verified

- Limit warm-up "reset confirmed" trigger compares before/after usage-window
  entries (`app/modules/limit_warmup/service.py`), never `accounts.reset_at`;
  a synthetic cooldown deadline cannot start a warm-up.
- `background_recovery_state_from_account` already seeds
  `runtime.cooldown_until` from persisted `reset_at` for `RATE_LIMITED` rows,
  so the usage-refresh reconcile path honors the persisted cooldown without
  changes, and it already refuses to auto-recover rows with `reset_at` NULL.
- The usage updater's direct recovery write
  (`_recover_quota_status_from_usage` in `app/modules/usage/updater.py`) used
  to flip `RATE_LIMITED` accounts back to `ACTIVE` — clearing the persisted
  `reset_at` — as soon as fresh usage showed available quota, which would let
  the default 60s refresh erase a long Retry-After cooldown (e.g. 20 minutes)
  before peers ever enforced it. It now returns early for rows with
  `blocked_at` set while `now < reset_at` (or the `blocked_at + 30s` legacy
  floor when `reset_at` is NULL). Fresh usage showing quota is not evidence
  the 429 ended: upstream throttles are not always quota-based. Rows without
  `blocked_at` (window-derived `RATE_LIMITED`) keep the fresh-usage recovery,
  and the marking replica can still recover early through the
  runtime-cooldown-gated selection path.
- The user-facing selector retry hint is clamped independently by
  `SELECTOR_RETRY_HINT_MAX_SECONDS` (300s).
- Peer selection caches (5s TTL in production) may still route to a freshly
  marked account until TTL expiry; that convergence gap is owned by the
  cache-invalidation-bus work and is not widened by this change.
- Zero-primary-capacity plans (e.g. `free`): the stale-window recovery rewrite
  in `_state_from_account` (from #909) flips a `RATE_LIMITED` seed to `ACTIVE`
  when fresh monthly/long-window usage shows quota. That rewrite is gated on
  the ORIGINAL persisted `status`/`blocked_at`/`reset_at`: a row marked by an
  actual 429 (`blocked_at` set) stays held until the persisted deadline (or
  the legacy `blocked_at + 30s` floor) elapses — fresh monthly quota is
  recovery evidence for stale window data, not for a running 429 cooldown.
  Rows without `blocked_at` (window-derived `RATE_LIMITED`) keep the existing
  recovery, and the marking replica's post-block fresh-usage early-recovery
  gate is unchanged.

## What stays replica-local (by design)

Error counts, error backoff, drain/probe health tiers, probe streaks, and
in-flight/lease pressure are per-replica advisory state. Persisting them would
add synchronous DB writes to the hot proxy error path for signals that
converge per replica within bounded time (three local errors trigger local
backoff). Persisted `status`/`reset_at`/`blocked_at` transitions are the only
cross-replica health signals.
