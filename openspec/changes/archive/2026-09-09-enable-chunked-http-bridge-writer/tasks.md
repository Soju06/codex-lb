## 1. Specification

- [x] 1.1 Specify explicit default-off writer selection and mixed-format
      rejection.
- [x] 1.2 Specify batch and terminal v2 atomicity.

## 2. Implementation

- [x] 2.1 Add and validate the writer-format setting.
- [x] 2.2 Add owner-fenced batch and terminal chunk repository operations.
- [x] 2.3 Route the event batcher through v1 or v2 without duplicating events.

## 3. Verification

- [x] 3.1 Cover v1 default, v2 batch/terminal replay, size caps, and format
      conflicts.
- [x] 3.2 Run focused bridge/batcher tests, Ruff, and type checks.
- [x] 3.3 Run strict OpenSpec validation and `git diff --check`.
