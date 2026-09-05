## 1. Implementation

- [x] 1.1 Add a detached-row predicate to the durable bridge coordinator matching the account-invalidation detach shape.
- [x] 1.2 Skip detached rows in `lookup_request_targets` for alias resolution and the canonical-key fallback.

## 2. Regression Coverage

- [x] 2.1 Cover a hard `thread_header` row detached by the real `_close_http_bridge_sessions_for_account` path: lookup returns no durable evidence and a fresh claim re-owns the row.
- [x] 2.2 Cover an ordinarily released `CLOSED` row that still names its account: lookup still returns it.

## 3. Verification

- [x] 3.1 Run the durable bridge, HTTP bridge and repository test slices plus lint and type-check gates.
- [x] 3.2 Run strict OpenSpec validation for this change.
