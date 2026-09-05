## Implementation
- [x] Reject migration adoption and correct historical schema fixtures.
- [x] Preserve typed fan-out errors and sibling cleanup.
- [x] Separate HTTP ownership binding from observed participation.
- [x] Correct WebSocket send marker ordering.

## Verification
- [x] Add regressions at migration, route and transport boundaries.
- [x] Run focused SQLite/PostgreSQL and affected transport checks.
- [x] Validate code and OpenSpec.

Validated with 58 focused SQLite tests, 57 migration unit tests, 196 PostgreSQL tests, and 676 additional migration/replay/transport regressions. PostgreSQL-only cases skipped in SQLite ran in the PostgreSQL target. Two legacy-fixture failures in the broader initial run were fixed and the complete migration unit suite reran successfully. Lint, types, architecture, cancellation and strict OpenSpec checks passed. Full CI is delegated to GitHub Actions.
