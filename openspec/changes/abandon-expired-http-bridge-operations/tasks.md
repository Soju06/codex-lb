## 1. Specification

- [x] 1.1 Define the terminal `abandoned` state and its duplicate-suppression
      and full-history recovery contract.
- [x] 1.2 Define cutoff, owner/pending protections, CAS fencing, retention,
      and observability requirements.

## 2. Implementation

- [x] 2.1 Add repository/coordinator abandonment selection and atomic CAS
      transition, treating `abandoned` as immutable in all operation writers.
- [x] 2.2 Add bounded bridge-heartbeat sweeping with canonical and detached
      pending-operation protection.
- [x] 2.3 Return canonical `previous_response_not_found` for abandoned
      continuations without recovery dispatch.
- [x] 2.4 Add structured logs and a low-cardinality abandonment counter.

## 3. Verification

- [x] 3.1 Add repository tests for stale transition, active owner/lease
      protection, pending-operation protection, updated-at/state CAS races,
      and late-writer fencing.
- [x] 3.2 Add request-admission regression proving an abandoned operation does
      not call upstream and returns the standard continuity error.
- [x] 3.3 Add heartbeat maintenance coverage and retention coverage for the
      new terminal state.
- [x] 3.4 Run focused tests, Ruff/type checks, and strict OpenSpec validation.
