## 1. Specification

- [x] 1.1 Define owner-bound reconciliation for verified durable full resends.
- [x] 1.2 Define proof integrity and preserved fail-closed boundaries.

## 2. Implementation

- [x] 2.1 Add a sealed, immutable, request-bound full-resend proof.
- [x] 2.2 Drop only broad session-header selection provenance and forwarded
      aliases while preserving durable owner routing and Codex session behavior.

## 3. Verification

- [x] 3.1 Add proof construction, mutation, state-substitution, and
      response-bound pending-tool-call regressions.
- [x] 3.2 Add bridge regressions for stale broad owner reconciliation,
      incremental fail-closed behavior, same-account routing, and stripped
      upstream aliases.
- [x] 3.3 Run focused bridge tests, Ruff, type checking, and strict OpenSpec
      validation.
