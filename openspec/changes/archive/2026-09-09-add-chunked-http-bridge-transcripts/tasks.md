## 1. Specification

- [x] 1.1 Specify versioned dual-format transcript reads and fail-closed
      decoding.
- [x] 1.2 Specify additive migration and guarded downgrade behavior.

## 2. Migration and codec

- [x] 2.1 Add `spool_format` and chunk ORM models plus a single-head Alembic
      revision.
- [x] 2.2 Implement deterministic bounded chunk encode/decode helpers.
- [x] 2.3 Add migration upgrade/downgrade and codec regression coverage.

## 3. Dual reader and lifecycle

- [x] 3.1 Dispatch transcript reads by format without changing the v1 writer.
- [x] 3.2 Make reset, retry, rollback, and retention delete/check both formats.
- [x] 3.3 Cover v1/v2 replay equivalence, corrupt chunks, and lifecycle cleanup.

## 4. Verification

- [x] 4.1 Run migration upgrade/check/downgrade coverage.
- [x] 4.2 Run focused bridge lifecycle, Ruff, and type checks.
- [x] 4.3 Run strict OpenSpec validation and `git diff --check`.
