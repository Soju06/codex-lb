## 1. Regression Coverage

- [x] 1.1 Add a failing fresh-selection test for a usage-only draining `burn_first` account with a selectable healthy fallback.
- [x] 1.2 Add exclusion tests for current error drain evidence, missing healthy fallback, and 100% exhausted candidates.
- [x] 1.3 Add regression coverage proving sticky and hard-continuity owner behavior is unchanged.

## 2. Selection Implementation

- [x] 2.1 Implement canonical usage-only drain classification without duplicating health thresholds.
- [x] 2.2 Add a default-off fresh-selection exception after recovery-probe admission and before health-tier narrowing.
- [x] 2.3 Enable the exception only for unbound selection and fresh soft-sticky keys with no existing mapping.

## 3. Verification

- [x] 3.1 Run targeted serial pytest for account selection and sticky/continuity behavior.
- [x] 3.2 Run formatting, lint, type, and strict OpenSpec validation applicable to the changed files.
- [x] 3.3 Review the final diff for unrelated behavior or member-auth 0% threshold changes.
