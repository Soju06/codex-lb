# Tasks

## 1. Regression Coverage

- [x] 1.1 Add a bridge integration regression driving a completed turn, an anchored turn denied with `previous_response_not_found`, then a following client full resend, asserting the third dispatch carries no `previous_response_id` and is not trimmed against the denied anchor.
- [x] 1.2 Add unit coverage that a denial retires both anchor carriers on the first occurrence, and that it is skipped when a concurrent completion has already advanced the anchor.
- [x] 1.4 Add unit coverage for the retirement decision: delta-only payloads and client-supplied anchors are left alone, a shared anchor is selected once from a fan-out, and a bookkeeping failure is swallowed.
- [x] 1.3 Assert at the product path that no continuity diagnostic reports a proxy-injected anchor as `client_supplied`, which fails before the provenance fix.
- [x] 1.5 Add a pre-dispatch regression proving a request that already captured a denied proxy-injected anchor is failed closed without sending another upstream frame.
- [x] 1.6 Cover the sibling-advanced race and prove the denied id is still tombstoned while the newer current anchor is preserved.
- [x] 1.7 Add a coordinated regression proving denial publication and prepared dispatch are lifecycle-serialized.
- [x] 1.8 Add route-level coverage proving a transient durable-clear failure is retried on the next turn without rehydrating, redispatching, or trimming against the tombstoned anchor.
- [x] 1.9 Prove tombstone rejection occurs before a reversible recovery alias is published.
- [x] 1.10 Add cancellation coverage proving a cancelled durable clear still unregisters the local alias and clears the in-memory carrier.
- [x] 1.11 Add fan-out coverage proving distinct eligible anchors make an unscoped denial ineligible for retirement.
- [x] 1.12 Add generation-race coverage proving detached denials reach a live successor and later successors inherit tombstones.
- [x] 1.13 Cover grouped ambiguity when the distinct anchor is client-supplied or delta-only.
- [x] 1.14 Add durable repository coverage proving a denial survives owner-epoch advance, removes only its matching alias, and preserves a newer anchor.
- [x] 1.15 Add cross-replica preparation and final-dispatch coverage proving a durable tombstone prevents request-level injection, recovery alias publication, and upstream send.
- [x] 1.16 Add a deterministic durable-coordinator regression proving a denial that starts after a clean dispatch recheck cannot commit until the dispatch fence releases, and that a denial that wins the fence prevents dispatch.
- [x] 1.17 Add submit-path coverage proving the final durable fence is held through the WebSocket send and a tombstone appearing after the initial recheck still prevents that send.
- [x] 1.18 Add submit-path coverage proving a wedged fenced send times out, releases the durable fence, and is classified as account-neutral liveness failure.
- [x] 1.19 Add coverage proving dispatch-fence entry failure rolls back a published recovery alias and retires the session when rollback cannot be confirmed.
- [x] 1.20 Add coverage proving local alias-unregister failure after a late tombstone cannot escape into account-penalizing send-failure handling.

## 2. Anchor Retirement

- [x] 2.1 Add `_invalidate_denied_http_bridge_anchor`, clearing only the matching durable response anchor and alias under the current owner fence while preserving turn-state and sibling response aliases; clear the in-memory carrier even when alias unregistering fails.
- [x] 2.2 Call it from the terminal `previous_response_not_found` branch when the denied anchor was proxy-injected onto a full-resend-shaped payload.
- [x] 2.3 Call it from the grouped fan-out branch as well, which settles every request sharing the anchor and returns before the single-request branch.
- [x] 2.4 Keep retirement best-effort so a bookkeeping failure cannot change how the denial is delivered downstream.
- [x] 2.5 Publish a session-local denied-anchor tombstone before the first await and reject any prepared proxy-injected request carrying that id immediately before dispatch.
- [x] 2.6 Publish the tombstone before checking whether a sibling already advanced the current anchor, so that check cannot reopen the dispatch race.
- [x] 2.7 Serialize tombstone publication with the submitter's final revalidation and send section.
- [x] 2.8 Retry a surviving tombstoned durable anchor during hydration and suppress the stale lookup regardless of the retry outcome.
- [x] 2.9 Move the lifecycle-serialized tombstone check before recovery alias registration so undispatched requests cannot leave stale routing state.
- [x] 2.10 Keep alias unregister and in-memory cleanup in the durable clear's cancellation-safe `finally` path.
- [x] 2.11 Require every retirement-eligible grouped request to agree on one anchor before retirement.
- [x] 2.12 Publish tombstones to live same-key successors, clear their matching local carrier, and inherit tombstones during successor registration.
- [x] 2.13 Require all non-null grouped anchors to agree before applying retirement eligibility.
- [x] 2.14 Persist denial tombstones independently of owner epoch in the existing durable alias table, while conditionally clearing only an exact current anchor and deleting only the denied response alias.
- [x] 2.15 Strip durable tombstones before request-level payload preparation and recheck them before reversible alias publication and final upstream dispatch.
- [x] 2.16 Acquire one durable session-row fence for the final tombstone check, hold it through the WebSocket send, and make denial retirement acquire the same fence before publishing its tombstone.
- [x] 2.17 Bound the fenced WebSocket send with the existing upstream transport timeout and classify expiry as account-neutral liveness failure so transaction unwind releases the durable fence.
- [x] 2.18 Roll back any published recovery turn-state alias when durable dispatch-fence entry fails, and retire the session when rollback cannot be confirmed.
- [x] 2.19 Contain local response-alias unregister failures after a late durable tombstone while still clearing matching in-memory trim state.

## 3. Recovery Provenance

- [x] 3.1 Copy `proxy_injected_previous_response_id` onto the anchored recovery retry state, gated on the retry actually carrying an anchor.
- [x] 3.2 Copy `proxy_injected_anchor_had_full_resend_payload` with it, so a replayed anchor keeps the shape that decides whether it may be retired.

## 4. Verification

- [x] 4.1 Run the touched bridge unit and integration suites, ruff, and type checks.
- [x] 4.2 Run strict OpenSpec validation for this change and review the final diff for unrelated changes.
- [x] 4.3 Re-run the touched bridge and durable-coordinator suites plus ruff, type checking, strict OpenSpec validation, and final diff review after adding the cross-replica dispatch fence.
- [x] 4.4 Re-run the touched bridge suites, ruff, type checking, strict OpenSpec validation, and final diff review after review-driven fence cleanup hardening.
