## Context

All proxy transports feed upstream failures through `classify_upstream_failure` and `_handle_stream_error`. Upstream uses `usage_limit_reached` for both primary and long-window exhaustion, so the error code alone cannot justify switching from `RATE_LIMITED` to `QUOTA_EXCEEDED`.

The state builder's early-recovery gate previously accepted any fresh primary sample, even at 100%, and did not reject an exhausted applicable long window unless the primary window had expired. Separately, explicit quota state could recover after its debounce while the long window remained exhausted, or use credits cached before the rejection.

## Goals / Non-Goals

**Goals:**

- Preserve upstream reset deadlines until expiry or valid early-recovery evidence.
- Require refreshed long-window usage to prove availability, rather than freshness alone, before an explicit quota state can recover.
- Preserve existing pre-visible failover and downstream error contracts.
- Leave ordinary transient rate-limit behavior unchanged.

**Non-Goals:**

- Inferring upstream account status from advisory usage snapshots alone.
- Relaxing account ownership or encrypted-content replay constraints.
- Adding retry attempts or configurable cooldowns.

## Decisions

- Keep `usage_limit_reached` and `rate_limit_exceeded` classified as `rate_limit`, preserving existing deadlines and dashboard semantics.
- Both error codes share the persisted `RATE_LIMITED` state; the evidence gate governs early usage-based recovery, not deadline expiry. It does not change unrelated health penalties. Unsupported monthly rows are ignored before evaluating long-window exhaustion, including raw rows supplied by background recovery.
- Require available usage in the sample used for early recovery. An exhausted long window cannot be hidden by an available primary sample. Preserve the existing newer-long-window recovery path after the primary reset expires.
- Preserve marking-replica versus peer behavior: only the replica with runtime evidence of the current rate-limit block can recover before its persisted deadline. Peers honor the deadline until a valid recovery is persisted.
- Preserve the public upstream code and response body. The change affects account-health state only, so clients continue receiving the original error if failover is unsafe or no replacement can be selected.
- Preserve an explicit quota state when fresh applicable long-window usage remains exhausted and no usable credit override exists. The observed long-window reset becomes the routing reset deadline, replacing a shorter fallback deadline when available.
- Discard an elapsed fallback deadline when the exhausted sample has no reset metadata. Selection then waits for quota recovery evidence without inventing another reset time.
- For explicit quota blocks with a persisted or runtime block timestamp, use only credit snapshots recorded strictly after that timestamp. Fresh quota-window data without credit fields cannot refresh the age of older credit evidence.

For example, a `usage_limit_reached` response with `resets_in_seconds=7200` is followed by primary usage at 100% and weekly usage at 40%. At both 130 seconds and one hour, neither replica may clear the two-hour hold merely because the sample is newer. If the marking replica later sees post-block primary usage at 40% with weekly capacity available, its existing recovery path may persist ACTIVE; a peer then observes that recovery.

## Risks / Trade-offs

- Stale exhausted samples can prevent early evidence-based recovery until a new available sample arrives. Ordinary deadline expiry remains unchanged; this change does not infer new blocks for active accounts from advisory usage alone.
- Classifier and transport tests retain the existing rate-limit contract while full-flow tests cover both exhausted windows and cached credits.
