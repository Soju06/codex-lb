# Tasks

## 1. Regression Coverage

- [x] 1.1 Add a bridge integration regression driving a completed turn, an anchored turn denied with `previous_response_not_found`, then a following client full resend, asserting the third dispatch carries no `previous_response_id` and is not trimmed against the denied anchor.
- [x] 1.2 Add unit coverage that a denial retires both anchor carriers on the first occurrence, and that it is skipped when a concurrent completion has already advanced the anchor.
- [x] 1.3 Assert at the product path that no continuity diagnostic reports a proxy-injected anchor as `client_supplied`, which fails before the provenance fix.

## 2. Anchor Retirement

- [x] 2.1 Add `_invalidate_denied_http_bridge_anchor`, clearing durable continuity through the existing fenced `_abandon_durable_http_bridge_continuity` write and the in-memory anchor fields together.
- [x] 2.2 Call it from the terminal `previous_response_not_found` branch when the denied anchor was proxy-injected.

## 3. Recovery Provenance

- [x] 3.1 Copy `proxy_injected_previous_response_id` onto the anchored recovery retry state, gated on the retry actually carrying an anchor.

## 4. Verification

- [x] 4.1 Run the touched bridge unit and integration suites, ruff, and type checks.
- [x] 4.2 Run strict OpenSpec validation for this change and review the final diff for unrelated changes.
