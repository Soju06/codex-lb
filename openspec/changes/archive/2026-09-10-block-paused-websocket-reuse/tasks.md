## 1. Dispatch guard

- [x] 1.1 Extend idle owner-switch handling to the shared account-unavailable snapshot without crossing ownership or interrupting accepted siblings.
- [x] 1.2 Recheck availability after admission and settle/log a late local rejection without account health penalties.

## 2. Regression and verification

- [x] 2.1 Cover ordinary reused WebSockets with movable and pinned requests, shared snapshot changes, accepted siblings, and pause during admission.
- [x] 2.2 Run relevant WebSocket tests, lint, and strict OpenSpec validation; sync context and archive after verification.

## 3. Production

- [x] 3.1 Deploy through the HA surge script and verify all backends and public readiness.
- [x] 3.2 Check post-rollout traffic for new dispatches through paused incident accounts and report observed errors.
