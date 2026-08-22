# Tasks

## 1. Regression Coverage

- [x] 1.1 Add a bridge integration regression driving a completed turn, an anchored turn denied with `previous_response_not_found`, then a following client full resend, asserting the third dispatch carries no `previous_response_id` and is not trimmed against the denied anchor.
- [x] 1.2 Add unit coverage that a denial retires both anchor carriers on the first occurrence, and that it is skipped when a concurrent completion has already advanced the anchor.
- [x] 1.4 Add unit coverage for the retirement decision: delta-only payloads and client-supplied anchors are left alone, a shared anchor is selected once from a fan-out, and a bookkeeping failure is swallowed.
- [x] 1.3 Assert at the product path that no continuity diagnostic reports a proxy-injected anchor as `client_supplied`, which fails before the provenance fix.
- [x] 1.5 Add a pre-dispatch regression proving a request that already captured a denied proxy-injected anchor is failed closed without sending another upstream frame.

## 2. Anchor Retirement

- [x] 2.1 Add `_invalidate_denied_http_bridge_anchor`, clearing only the matching durable response anchor and alias under the current owner fence while preserving turn-state and sibling response aliases; clear the in-memory carrier even when alias unregistering fails.
- [x] 2.2 Call it from the terminal `previous_response_not_found` branch when the denied anchor was proxy-injected onto a full-resend-shaped payload.
- [x] 2.3 Call it from the grouped fan-out branch as well, which settles every request sharing the anchor and returns before the single-request branch.
- [x] 2.4 Keep retirement best-effort so a bookkeeping failure cannot change how the denial is delivered downstream.
- [x] 2.5 Publish a session-local denied-anchor tombstone before the first await and reject any prepared proxy-injected request carrying that id immediately before dispatch.

## 3. Recovery Provenance

- [x] 3.1 Copy `proxy_injected_previous_response_id` onto the anchored recovery retry state, gated on the retry actually carrying an anchor.
- [x] 3.2 Copy `proxy_injected_anchor_had_full_resend_payload` with it, so a replayed anchor keeps the shape that decides whether it may be retired.

## 4. Verification

- [x] 4.1 Run the touched bridge unit and integration suites, ruff, and type checks.
- [x] 4.2 Run strict OpenSpec validation for this change and review the final diff for unrelated changes.
