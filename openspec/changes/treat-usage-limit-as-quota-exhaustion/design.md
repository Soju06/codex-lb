## Context

All proxy transports feed upstream failures through `classify_upstream_failure` and `_handle_stream_error`. The classifier currently groups `usage_limit_reached` with `rate_limit_exceeded`, so `_handle_stream_error` calls `mark_rate_limit`. Without reset metadata, that path has a short process-local cooldown and permits early recovery from fresh usage data.

After quota classification, the load-balancer state builder has a second recovery defect: once the 120-second quota debounce expires, any fresh long-window sample clears the runtime reset guard. `apply_usage_quota(..., infer_status_from_usage=False)` then changes the explicit `QUOTA_EXCEEDED` state to `ACTIVE` even when that same sample still reports 100% long-window usage. Every account-selection pass persists the false recovery, so the exhausted account repeatedly returns to rotation.

## Goals / Non-Goals

**Goals:**

- Keep accounts that return `usage_limit_reached` out of routing until quota recovery rules admit them.
- Require refreshed long-window usage to prove availability, rather than freshness alone, before an explicit quota state can recover.
- Preserve existing pre-visible failover and downstream error contracts.
- Leave ordinary transient rate-limit behavior unchanged.

**Non-Goals:**

- Inferring upstream account status from advisory usage snapshots alone.
- Relaxing account ownership or encrypted-content replay constraints.
- Adding retry attempts or configurable cooldowns.

## Decisions

- Classify `usage_limit_reached` as `quota` in the shared classifier. This makes every transport use the existing `mark_quota_exceeded` path without transport-specific branches.
- Keep `rate_limit_exceeded` classified as `rate_limit`. A temporary throttle and an exhausted usage allowance have different recovery evidence and must not share the same health mutation.
- Preserve the public upstream code and response body. The change affects account-health state only, so clients continue receiving the original error if failover is unsafe or no replacement can be selected.
- Preserve an explicit quota state when fresh applicable long-window usage remains exhausted and no usable credit override exists. The observed long-window reset becomes the routing reset deadline, replacing a shorter fallback deadline when available.

The alternative was to lengthen the generic rate-limit cooldown. That would delay genuine short throttles and would still allow the account to recover without proving that its exhausted long window is available.

## Risks / Trade-offs

- A `usage_limit_reached` response without reset metadata may hold an account conservatively until usage refresh proves recovery. Freshness alone is insufficient; the applicable long window must be below 100% or a usable credit override must exist. This is preferable to repeatedly routing work to an account that upstream has explicitly rejected for exhausted usage.
- Existing tests that equate `usage_limit_reached` with a generic rate limit must be updated to assert quota-health handling while retaining failover coverage.
