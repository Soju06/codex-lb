## 1. Reliable publication primitive

- [x] 1.1 Make awaited `bump()` retain failed, cancelled, and ambiguous writes in the poller's pending set.
- [x] 1.2 Preserve a new marker requested while an immediate or coalesced write is in flight.
- [x] 1.3 Keep callback acknowledgement and `bump_local()` behavior unchanged after a successful write.

## 2. Remove compensating call-site complexity

- [x] 2.1 Delete the route-cache-specific fallback `request_bump()`.
- [x] 2.2 Remove the redundant `upstream_route` bump from upstream-routing settings changes; retain synchronous local clearing and peer clearing through `settings`.
- [x] 2.3 Correct stale comments and stable query-caching documentation.

## 3. Regression coverage and verification

- [x] 3.1 Cover exhausted retries, unexpected failure, cancellation, in-flight marker races, and later recovery.
- [x] 3.2 Cover real API-key policy and account-routing mutation paths through peer caches.
- [x] 3.3 Cover settings-driven local and peer route-cache clearing without a duplicate namespace bump.
- [x] 3.4 Pass focused tests, relevant regression suites, static checks, and strict OpenSpec validation.
