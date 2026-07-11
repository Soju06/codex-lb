# Tasks

## Specification

- [x] Reconcile account switching with the existing full-resend and hard-owner contracts.

## Implementation

- [x] Add a fail-closed account-switch request preparation helper.
- [x] Reselect only fresh or proxy-verified unsent WebSocket turns.
- [x] Reconnect mismatched owner-pinned turns without stripping their anchor.
- [x] Permit quota failover only for proxy-injected verified anchors.
- [x] Permit WS-to-HTTP failover only with matching session continuity fingerprints.

## Verification

- [x] Add helper, selector, owner-pin, and quota replay regressions.
- [x] Add verified and unverified cross-transport replay regressions.
- [x] Run focused and broad WebSocket tests.
- [x] Run Ruff, type checks, strict OpenSpec validation, and diff checks.
