## Implementation
- [x] Reject migration adoption and correct historical schema fixtures.
- [x] Preserve typed fan-out errors and sibling cleanup.
- [x] Separate HTTP ownership binding from observed participation.
- [x] Correct WebSocket send marker ordering.

## Verification
- [x] Add regressions at migration, route and transport boundaries.
- [x] Run focused SQLite/PostgreSQL and affected transport checks.
- [x] Validate code and OpenSpec.

Validation runs reported 58 focused SQLite passes, 57 migration unit passes, 196 PostgreSQL passes, and 676 additional migration/replay/transport passes. These are per-run counts, not distinct tests: the suites overlap, including SQLite/PostgreSQL executions of the same tests and reruns after fixes. They must not be summed into a unique-test total. The final context-only rerun passed 25 tests already represented by the focused suites. PostgreSQL-only cases skipped in SQLite ran in the PostgreSQL target. Two legacy-fixture failures in the broader initial run were fixed and the complete migration unit suite reran successfully. Lint, types, architecture, cancellation and strict OpenSpec checks passed. Full CI is delegated to GitHub Actions.
