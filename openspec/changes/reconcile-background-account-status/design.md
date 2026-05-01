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

### Seed a synthetic expired cooldown for background-only recovery evaluation

For scheduler evaluation, create a throwaway `RuntimeState` whose `blocked_at` mirrors persisted `accounts.blocked_at` and whose `cooldown_until` is treated as already expired when a persisted block marker exists. This lets `_state_from_account()` clear a stale `rate_limited` reset guard when a fresh post-block primary usage row proves recovery, without depending on live process memory.

Alternative considered: require the real runtime cooldown to exist. Rejected because the scheduler has no access to another process's in-memory balancer state and would not fix the stuck persisted row.

### Persist only recovery transitions

The scheduler MUST only write when a currently blocked account evaluates to `active`. On recovery, persist `status=active`, `reset_at=NULL`, `blocked_at=NULL`, and `deactivation_reason=NULL` through `update_status_if_current()` so concurrent request-path state changes still win.

Alternative considered: update only `status`. Rejected because leaving stale reset/block markers on an active row creates inconsistent persisted state.

## Risks / Trade-offs

- Synthetic cooldown expiry can recover a blocked account earlier than request-path retry backoff would have allowed. Mitigation: recovery still requires fresh post-block usage rows under 100%, so stale pre-block rows cannot trigger it.
- Scheduler reconciliation and request-path persistence can race. Mitigation: use `update_status_if_current()` and skip writes when the stored row changed underneath the scheduler.
- `_state_from_account()` remains a privateish load-balancer primitive. Mitigation: wrap scheduler reuse in a dedicated helper with a narrow contract instead of importing internal pieces into the scheduler.
