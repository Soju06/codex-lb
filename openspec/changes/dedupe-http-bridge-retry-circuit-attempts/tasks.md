## 1. Specification

- [x] 1.1 Add the attempt-scoped retry-circuit requirement and race scenarios.
- [x] 1.2 Validate the change in strict mode.

## 2. Implementation

- [x] 2.1 Add the process-local HTTP bridge send-attempt state and lifecycle transitions.
- [x] 2.2 Capture and thread the classified attempt through all retry-circuit failure paths.
- [x] 2.3 Claim the attempt atomically with the circuit increment and suppress duplicate persistence.
- [x] 2.4 Add low-cardinality duplicate-suppression observability without new settings or schema.

## 3. Coverage

- [x] 3.1 Add a full HTTP bridge regression where reader and downstream watchdogs observe one send.
- [x] 3.2 Prove a new send is a new strike and a delayed observer of an old send is not.
- [x] 3.3 Cover response-wins, send-failure/cancellation, successful reset, and multiple-pending races.
- [x] 3.4 Preserve clean-close, continuity, owner-handoff, and durable conflict-merge coverage.

## 4. Verification

- [x] 4.1 Run focused HTTP bridge and durable retry-circuit tests.
- [x] 4.2 Run Ruff, formatting checks, and the full unit suite.
- [x] 4.3 Validate the OpenSpec change strictly and validate all main specs.
- [x] 4.4 Review the final diff for lock ordering, persistence cardinality, scope creep, and rollback compatibility.
