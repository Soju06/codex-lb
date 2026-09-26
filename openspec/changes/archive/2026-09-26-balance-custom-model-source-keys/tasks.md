## 1. Implementation

- [x] 1.1 Add eligible candidate lookup and scoped response-source ownership resolution.
- [x] 1.2 Add bounded replica-local least-in-flight rotation and cooldown selection.
- [x] 1.3 Integrate bounded safe failover with per-attempt settlement and cancellation cleanup on both Responses routes.

## 2. Validation and documentation

- [x] 2.1 Add real-route regression coverage for rotation, alias/key mapping, scope, saturation, cooldown, failover, continuity and no-replay boundaries.
- [x] 2.2 Run existing source routing/dispatch/alias and invariant checks, lint/type checks and strict OpenSpec validation.
- [x] 2.3 Document setup and limitations, synchronize specs, verify and archive.
