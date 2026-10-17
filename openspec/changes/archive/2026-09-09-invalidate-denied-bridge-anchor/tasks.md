# Tasks

## 1. Regression Coverage

- [x] 1.1 Add a bridge integration regression driving a completed turn, an anchored turn denied with `previous_response_not_found`, then a following client full resend, asserting the third dispatch carries no `previous_response_id` and is not trimmed against the denied anchor.
- [x] 1.2 Add unit coverage that a denial retires both anchor carriers on the first occurrence, and that it is skipped when a concurrent completion has already advanced the anchor.
- [x] 1.4 Add unit coverage for the retirement decision: delta-only payloads and client-supplied anchors are left alone, a shared anchor is selected once from a fan-out, and a bookkeeping failure is swallowed.
- [x] 1.3 Assert at the product path that no continuity diagnostic reports a proxy-injected anchor as `client_supplied`, which fails before the provenance fix.
- [x] 1.5 Add a pre-dispatch regression proving a request that already captured a denied proxy-injected anchor is failed closed without sending another upstream frame.
- [x] 1.6 Cover the sibling-advanced race and prove the denied id is still tombstoned while the newer current anchor is preserved.
- [x] 1.7 Add a coordinated regression proving denial publication and prepared dispatch are lifecycle-serialized.
- [x] 1.8 Add a product-path regression proving a detached predecessor fences a durable-anchor capture made before successor session creation, and a ledger-pruning regression proving active entries survive the bound.
- [x] 1.9 Add regressions proving equal-generation durable recaptures after failed cleanup and owner-forward recovery injections retain an existing denial and fail closed before dispatch.
- [x] 1.10 Add product-path coverage for admitted denials after session close and for historical sibling fences retiring on ownerless close.
- [x] 1.11 Add owner-ordering coverage proving a local predecessor cannot replace a newer durable denial fence.

## 2. Anchor Retirement

- [x] 2.1 Add `_invalidate_denied_http_bridge_anchor`, clearing only the matching durable response anchor and alias under the current owner fence while preserving turn-state and sibling response aliases; clear the in-memory carrier even when alias unregistering fails.
- [x] 2.2 Call it from the terminal `previous_response_not_found` branch when the denied anchor was proxy-injected onto a full-resend-shaped payload.
- [x] 2.3 Call it from the grouped fan-out branch as well, which settles every request sharing the anchor and returns before the single-request branch.
- [x] 2.4 Keep retirement best-effort so a bookkeeping failure cannot change how the denial is delivered downstream.
- [x] 2.5 Publish a session-local denied-anchor tombstone before the first await and reject any prepared proxy-injected request carrying that id immediately before dispatch.
- [x] 2.6 Publish the tombstone before checking whether a sibling already advanced the current anchor, so that check cannot reopen the dispatch race.
- [x] 2.7 Serialize tombstone publication with the submitter's final revalidation and send section.
- [x] 2.8 Retain denied-id generations in a bounded process-local ledger and fail closed when a prepared request's captured generation is superseded, including when no canonical session existed at capture time.
- [x] 2.9 Treat an already-recorded denial as a hard observation for later durable captures and owner-forward recovery injections, even when no generation advance is observed by the new request.
- [x] 2.10 Preserve positive denial generations until the last active request pin releases, including after durable clear success or alias-unregister failure.
- [x] 2.11 Retire no-durable-owner sibling-race slots and stale session/epoch owner slots without clearing a successor fence.
- [x] 2.12 Keep local-only alias cleanup retries tracked when no durable owner exists, and distinguish unresolved current cleanup from historical sibling fences during close.
- [x] 2.13 Retain an unpinned stale-predecessor denial fence until a current owner confirms the matching durable anchor is cleared.
- [x] 2.14 Bound late-predecessor churn to the newest unpinned slot per owner, preserve older active pins until release, and retire retained predecessors after a newer durable clear.

## 3. Recovery Provenance

- [x] 3.1 Copy `proxy_injected_previous_response_id` onto the anchored recovery retry state, gated on the retry actually carrying an anchor.
- [x] 3.2 Copy `proxy_injected_anchor_had_full_resend_payload` with it, so a replayed anchor keeps the shape that decides whether it may be retired.

## 4. Verification

- [x] 4.1 Run the touched bridge unit and integration suites, ruff, and type checks.
- [x] 4.2 Run strict OpenSpec validation for this change and review the final diff for unrelated changes.
