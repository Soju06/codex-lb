# Verification

## Initial verification

- Baseline regression selection: 6 failed, 2 passed.
- Focused planner unit and API tests after the fix: 56 passed.
- Ruff lint/format and full `ty check`: passed.
- Strict change and affected main-spec validation: passed.
- Independent review: no actionable findings.
- Full `uv run pre-commit run local-ci --hook-stage manual --all-files`
  completed frontend lint, type check, tests, and build, then stopped at the
  unchanged upstream migration topology: two heads and a duplicate
  `20260914_000000` timestamp. Upstream CI at `9637bdee3` has the same
  migration failure. The fix does not modify migrations; remaining full-gate
  stages did not run.

The scoped change was verified; the repository-wide gate was blocked by the
pre-existing migration lineage tracked in upstream PRs #2461 and #2462.

## Verification after the upstream migration repair

Upstream #2461 is now included through main `09a140fa9`. The full
`uv run pre-commit run local-ci --hook-stage manual --all-files --verbose`
passed on Linux/arm64 at `8566e1a09`, including SQLite and PostgreSQL tests and
migration checks, packaging, Docker checks, Helm validation, and both kind
smoke scenarios. The run passed 1,643 frontend tests and 14,008 Python tests;
existing skips and the expected failure were unchanged.

Live HTTP checks also confirmed atomic rejection of malformed timezone updates,
trimming of valid names, retention for null/blank updates, and successful UTC
forecast fallback for a malformed stored key. Strict OpenSpec 1.11.0 validation
passed for the affected artifacts and all 66 main specs. Disposable databases
and the test cluster were removed, and the pre-existing Docker tags restored.

The inherited local-gate blocker is resolved. Current-head GitHub checks and
review state remain the public readiness evidence for the PR.
