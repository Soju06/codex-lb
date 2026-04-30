## Context

The request path already knows how to derive effective account state from persisted account rows plus the latest usage snapshots. That logic lives in `_state_from_account()` in the load balancer. The bug is that persisted account status is only corrected when request-path selection runs, while the background usage scheduler only refreshes usage rows and invalidates caches.

The change needs to stay narrow: only recover already-blocked accounts, never make fresh blocking decisions in the scheduler, and avoid load-balancer runtime side effects. The exact stuck case includes `rate_limited` rows whose persisted `reset_at` remains in the future even though a fresh primary usage row recorded after the block event is already below 100%.

## Goals / Non-Goals

**Goals:**
- Reconcile persisted `rate_limited` and `quota_exceeded` accounts back to `active` after background usage refresh writes fresh recovery data.
- Reuse existing account-state derivation rules instead of inventing a second status model.
- Keep scheduler writes limited to persisted account recovery fields: `status`, `reset_at`, `blocked_at`, and `deactivation_reason`.
- Cover both a unit-level scheduler reconciliation path and a SQLite-backed stale-status reproduction.

**Non-Goals:**
- Do not let the scheduler promote `active` accounts into `rate_limited` or `quota_exceeded`.
- Do not change request-path selection, sticky-session routing, or dashboard payload shape.
- Do not add new persisted cooldown columns or new background jobs.

## Decisions

### Reuse load-balancer state derivation from the scheduler

Add a small helper in `app/modules/proxy/load_balancer.py` that evaluates recoverable background state for one persisted account using `_state_from_account()` and a synthetic runtime snapshot derived only from persisted markers. This keeps the scheduler aligned with existing quota semantics while isolating the background use case from live balancer runtime mutation.

Alternative considered: implement a second pure reconciliation function in the scheduler. Rejected because it would duplicate the nuanced weekly/primary-secondary usage normalization already handled in the load balancer.

### Seed the persisted cooldown deadline for background-only recovery evaluation

For scheduler evaluation, create a throwaway `RuntimeState` whose `blocked_at` mirrors persisted `accounts.blocked_at`. For `rate_limited` accounts, seed `cooldown_until` from the persisted reset deadline (`accounts.reset_at`) instead of treating the cooldown as already expired. This keeps scheduler reconciliation aligned with the original retry window while still letting `_state_from_account()` recover the account once the persisted cooldown deadline has actually elapsed and a fresh post-block primary usage row proves recovery.

If a `rate_limited` row has no persisted reset deadline, the scheduler must leave it blocked. The background path has no authoritative cooldown duration in that case, so it cannot safely clear the status without risking an early reactivation.

Alternative considered: always synthesize an expired cooldown. Rejected because it can reactivate a still-cooling `rate_limited` account as soon as any fresh primary usage row arrives, causing premature retries and repeated `429` loops.

### Persist only recovery transitions

The scheduler MUST only write when a currently blocked account evaluates to `active`. On recovery, persist `status=active`, `reset_at=NULL`, `blocked_at=NULL`, and `deactivation_reason=NULL` through `update_status_if_current()` so concurrent request-path state changes still win.

Alternative considered: update only `status`. Rejected because leaving stale reset/block markers on an active row creates inconsistent persisted state.

## Risks / Trade-offs

- Background recovery for `rate_limited` rows now depends on a persisted reset deadline. Trade-off: rows created without `reset_at` will remain blocked until a request-path runtime or operator clears them. Mitigation: this is safer than guessing a cooldown duration and prematurely reactivating a still-cooling account.
- Scheduler reconciliation and request-path persistence can race. Mitigation: use `update_status_if_current()` and skip writes when the stored row changed underneath the scheduler.
- `_state_from_account()` remains a privateish load-balancer primitive. Mitigation: wrap scheduler reuse in a dedicated helper with a narrow contract instead of importing internal pieces into the scheduler.
