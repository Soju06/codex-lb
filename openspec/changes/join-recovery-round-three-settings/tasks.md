## Implementation

- [x] Add a forward metadata-only merge with both existing heads as parents.
- [x] Extend graph and populated parent round-trip regression tests.
- [x] Validate migrations, tests, and OpenSpec before recording completion.

Local verification: 131 migration tests passed (7 PostgreSQL-only skips),
disposable SQLite upgrade reached the combined head, migration policy and
schema drift checks passed, and strict change validation plus all 64 specs passed.
Cloud CI and reviewer results are tracked against the pushed SHA in PR #1900.
