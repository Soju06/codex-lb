## 1. Regression Coverage

- [x] 1.1 Add a real subprocess matrix for explicit-empty `--db-url` across all six migration subcommands and assert the configured fallback database is neither created nor mutated.
- [x] 1.2 Add an omitted-option subprocess control that upgrades the configured settings target to the current migration head.
- [x] 1.3 Run the new regression against the pre-fix implementation and record the expected explicit-empty failures while the omitted fallback control passes.

## 2. CLI Target Validation

- [x] 2.1 Reject an explicitly supplied exact empty `--db-url` during argument parsing without changing non-empty or whitespace-only values.
- [x] 2.2 Resolve the settings fallback only when the parsed `--db-url` value is `None`.

## 3. Focused Verification

- [x] 3.1 Run the migration subprocess regression green and confirm all six explicit-empty cases produce argument errors without fallback side effects while omission still migrates the configured target.
- [x] 3.2 Run focused migration tests plus lint and type checks for the touched Python scope.
- [x] 3.3 Validate the active OpenSpec change strictly and verify implementation, requirements, design, and completed tasks are coherent without archiving the change.
