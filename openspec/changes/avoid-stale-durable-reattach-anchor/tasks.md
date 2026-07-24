## 1. Specification

- [x] 1.1 Define verified full-resend behavior for fresh durable bridge reattach.

## 2. Implementation

- [x] 2.1 Keep durable owner routing while skipping stale anchor injection for a verified full resend on a fresh bridge.
- [x] 2.2 Prevent the stale durable response ID from being re-injected at session level on that path.

## 3. Verification

- [x] 3.1 Add a regression proving a verified full resend is submitted complete and unanchored on fresh reattach.
- [x] 3.2 Preserve regression coverage proving incremental reattach still injects the durable anchor.
- [x] 3.3 Run focused tests, Ruff, and OpenSpec validation where available.
