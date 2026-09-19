## 1. Durable metadata

- [x] 1.1 Add a failing public request-log contract test, then implement nullable persistence/API fields and verify it passes.
- [x] 1.2 Add the unique forward migration and verify single-head, populated upgrade/downgrade, and null historical metadata.

## 2. Existing routing decisions

- [x] 2.1 Prove HTTP routing-to-log metadata red then green for explicit, derived, absent, and failure cases.
- [x] 2.2 Carry immutable per-turn observation through native WebSocket and bridge final/failure emitters; prove public route logging and isolation between turns.
- [x] 2.3 Carry compact final observation and verify its public success/failure paths.

## 3. Delivery

- [x] 3.1 Run relevant regression and required checks, inspect exact base/candidate diff, and validate OpenSpec strictly.
- [x] 3.2 Document hash format, row granularity, absent observations, and privacy in the owning capability context.

PR publication, hosted verification, and readiness acceptance are tracked in the operations delivery receipt after local verification.
