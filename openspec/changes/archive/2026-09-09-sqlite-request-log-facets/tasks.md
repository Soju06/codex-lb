## 1. Reproduce and fix

- [x] 1.1 Add a synthetic SQLite options-endpoint regression and prove that the current candidate exceeds its query-work bound.
- [x] 1.2 Separate SQLite traversal from eligibility and verify the regression passes with unchanged option contents and ordering.

## 2. Verify and deliver

- [x] 2.1 Run the existing options-endpoint suite, relevant repository tests, lint/type checks, and strict OpenSpec validation with both database URLs pinned to a dedicated temporary file.
- [x] 2.2 Review the exact diff and record the benchmark and live-evidence limits for spec sync and archival.
