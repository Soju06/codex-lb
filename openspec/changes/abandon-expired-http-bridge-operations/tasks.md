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
- [x] 2.5 Keep protected-operation sweep predicates database-safe and page
      oversized protection snapshots without truncating them.
- [x] 2.6 Bound each oversized-protection scan slice and resume from a
      coordinator-owned keyset cursor across heartbeats.
- [x] 2.7 Add one durable lease period of cross-replica grace before an owned
      operation becomes eligible, and emit the specified abandonment reason.
- [x] 2.8 Apply the same grace to recently released ownerless sessions, retain
      PostgreSQL row locking on oversized sweeps, and return a distinct
      parameterless full-history contract for abandoned hard turn-state work.
- [x] 2.9 Fence the shared `chunks_v2` lock helper so a lease-expired owner's
      late chunk batch or terminal chunk cannot resurrect an `abandoned` row.
- [x] 2.10 Compare the CAS inactivity clock against the cutoff instead of the
      loaded value so SQLite's second-precision `onupdate` timestamps match,
      and release a journaled recovery checkpoint before the abandoned
      rejection.

## 3. Verification

- [x] 3.1 Add repository tests for stale transition, active owner/lease
      protection, pending-operation protection, updated-at/state CAS races,
      and late-writer fencing.
- [x] 3.2 Add request-admission regression proving an abandoned operation does
      not call upstream and returns the standard continuity error.
- [x] 3.3 Add heartbeat maintenance coverage and retention coverage for the
      new terminal state.
- [x] 3.4 Run focused tests, Ruff/type checks, and strict OpenSpec validation.
- [x] 3.5 Cover oversized protection snapshots while preserving cleanup of
      unrelated stale operations.
- [x] 3.6 Cover a protected prefix larger than the scan budget and prove that a
      later sweep resumes and cleans up a later unprotected operation.
- [x] 3.7 Cover the lease-expiry grace and the structured abandonment reason.
- [x] 3.8 Cover ownerless-release grace, oversized-path row locking, and
      abandoned hard turn-state recovery.
- [x] 3.9 Cover late `chunks_v2` batch and terminal chunk writes from the
      original lease-expired owner against an abandoned operation.
- [x] 3.10 Cover a row aged in SQLite's `CURRENT_TIMESTAMP` text form after a
      real ORM event append, and the recovery-journal rollback on the abandoned
      rejection path.
