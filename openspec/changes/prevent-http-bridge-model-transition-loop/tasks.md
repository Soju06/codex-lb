## 1. Implementation

- [x] 1.1 Preserve internal parallel fork keys and session-header fallback state across lookup-loop iterations.
- [x] 1.2 Protect incompatible session-header parents from capacity eviction while creating an isolated model child.

## 2. Validation

- [x] 2.1 Add regressions for an unmatched generated turn state with an incompatible session-header fallback, missing previous-response lookup, full-cache eviction, and an in-flight parent completing before isolation.
- [x] 2.2 Run focused and full repository validation, including strict OpenSpec validation.
