# Tasks

## Specification

- [x] Reconcile account switching with the existing full-resend and hard-owner contracts.

## Implementation

- [x] Add a fail-closed account-switch request preparation helper.
- [x] Reselect only fresh or proxy-verified unsent WebSocket turns.
- [x] Reconnect mismatched owner-pinned turns without stripping their anchor.
- [x] Permit quota failover only for proxy-injected verified anchors.
- [x] Permit WS-to-HTTP failover only with matching session continuity fingerprints.
- [x] Exclude failed owners and release account-local leases before verified replay.
- [x] Preserve file pins and client-owned anchors across security routing.
- [x] Drop retired-account turn-state before an owner-mismatch reconnect.
- [x] Replay durable trimmed HTTP bridge full resends after owner quota.
- [x] Exclude stalled owners after stripping HTTP bridge proxy anchors.
- [x] Refuse security-work account switches for file-backed retained bodies.
- [x] Move verified HTTP full resends past owner refresh/connect failures.
- [x] Move only file-free unanchored HTTP bridge retries away from stalled owners.

## Verification

- [x] Add helper, selector, owner-pin, and quota replay regressions.
- [x] Add verified and unverified cross-transport replay regressions.
- [x] Add file-pin, security, turn-state, sticky-exclusion, and HTTP bridge quota regressions.
- [x] Add stripped-anchor, file-backed security, and refresh/connect owner-failure regressions.
- [x] Run focused and broad WebSocket tests.
- [x] Run Ruff, type checks, strict OpenSpec validation, and diff checks.
