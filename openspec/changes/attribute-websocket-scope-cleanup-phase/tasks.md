## 1. Implementation

- [x] 1.1 Track the current fixed WebSocket scope cleanup phase.
- [x] 1.2 Include the phase in the existing cleanup-budget warning without
      changing cleanup control flow or timeout behavior.

## 2. Validation

- [x] 2.1 Add a route-level regression proving a blocked request-finalization
      cleanup is attributed to `pending_requests`.
- [x] 2.2 Run focused WebSocket tests, proxy integration tests, lint, type
      checks, architecture checks, and strict OpenSpec validation.
