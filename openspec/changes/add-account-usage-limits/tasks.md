# Tasks

- [x] 1. Add OpenSpec proposal, design, tasks, and account-routing delta requirements.
- [x] 2. Add the reusable standard-usage limit evaluator and unit coverage.
- [x] 3. Add account persistence fields and a reversible Alembic migration with migration tests.
- [x] 4. Add account schemas, repository/service update path, API endpoint, summary mapping, and backend tests.
- [x] 5. Carry standard quota rows into account selection and enforce one canonical hard gate across normal, sticky, additional-quota, opportunistic, fair-share, continuity-pinned HTTP/WebSocket, and synthetic-warmup paths.
- [x] 6. Add dashboard schemas, client mutation, account controls, main-card status presentation, mocks, and frontend tests while preserving API-valid percentage precision.
- [x] 7. Document configuring and interpreting per-account usage limits in the routing guide.
- [x] 8. Run focused backend/frontend, migration, precision, telemetry-freshness, error-precedence, continuity, and warmup regressions plus lint, formatting, type, architecture, and OpenSpec checks.
- [x] 9. Resolve final review findings for warmup spec ownership, opportunistic error precedence, dashboard blocked-state display, and accessible percentage validation.
- [x] 10. Make disable-retain atomic across the account API and dashboard, with stale-client regressions.

## Authorization and consistency revamp

- [x] 11. Reproduce final-selection, routing-context, telemetry-quality, and frontend-ordering regressions and integrate them into the owning test suites.
- [x] 12. Introduce an explicit fresh owner-authorization contract and prove final-attempt, continuity, warmup, and cancellation cleanup.
- [x] 13. Preserve canonical routing-pool context separately from capacity projections; prove disabled-policy fallback equivalence.
- [x] 14. Centralize measurement-quality filtering before historical calculations and verify genuine zero observations remain measurements.
- [x] 15. Classify authorization infrastructure failures as local in complete request-log metadata.
- [x] 16. Prevent stale reads and overlapping mutations from reverting acknowledged dashboard policy changes on the locked frontend dependencies.
- [x] 17. Document consistency and architectural decisions; run backend/frontend/PostgreSQL/spec/migration/quality checks.
