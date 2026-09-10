## 1. Reproduce and repair

- [x] 1.1 Reproduce the two-head failure with the public upgrade CLI in a disposable database.
- [x] 1.2 Add populated-parent and merge-only downgrade regression coverage.
- [x] 1.3 Add a schema-neutral merge revision without changing existing history.

## 2. Verify

- [x] 2.1 Verify CLI upgrade/check, parent data preservation, downgrade/re-upgrade and existing receipt/spool controls.
- [x] 2.2 Run affected checks, strict OpenSpec validation and independent Medium review.
- [x] 2.3 Publish with normal history and verify current-head hosted checks before handing monitoring back.

Verified source candidate5e75b778c passed48 local migration tests,52 receipt/anchor/prewarm controls and218 hosted PostgreSQL tests. All32 hosted checks succeeded with one skipped. Independent affected review found no new defect. Receipt-policy acceptance remains separate.
