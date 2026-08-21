## 1. Contract

- [x] 1.1 Define the exact conflict, local-request, payload-neutrality, and
  owner-preservation gates.
- [x] 1.2 Add the sticky-session exception without weakening unrelated hard
  owner conflicts.

## 2. Implementation

- [x] 2.1 Add the guarded account-neutral model-transition child-lane path.
- [x] 2.2 Pin the child lane key strength explicitly instead of relying on the
  implicit default.
- [x] 2.3 Reset the child request state's parent-derived affinity policy,
  continuity anchor, and reused parent turn state.
- [x] 2.4 Add positive and forwarded/unpinned-file/post-compaction negative
  regressions plus the single-retry bound.

## 3. Verification

- [x] 3.1 Run model-transition unit and existing HTTP bridge integration tests.
- [x] 3.2 Run Ruff, type checks, and strict OpenSpec validation.
