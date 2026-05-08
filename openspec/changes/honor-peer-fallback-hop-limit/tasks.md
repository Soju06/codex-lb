## 1. Runtime

- [x] 1.1 Change peer fallback eligibility to compare inbound fallback depth with `peer_fallback_max_hops`.
- [x] 1.2 Preserve loop prevention when inbound fallback depth is at or above the configured limit.

## 2. Verification

- [x] 2.1 Add unit coverage for multi-hop forwarding below the limit.
- [x] 2.2 Add unit coverage for rejection at the limit.
- [x] 2.3 Run focused backend checks and OpenSpec validation.
