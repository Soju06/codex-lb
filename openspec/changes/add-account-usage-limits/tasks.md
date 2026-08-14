# Tasks

- [x] 1. Add OpenSpec proposal, design, tasks, and account-routing delta requirements.
- [x] 2. Add the reusable standard-usage limit evaluator and unit coverage.
- [x] 3. Add account persistence fields and a reversible Alembic migration with migration tests.
- [x] 4. Add account schemas, repository/service update path, API endpoint, summary mapping, and backend tests.
- [x] 5. Carry standard quota rows into account selection and enforce the limit as a canonical hard gate across normal, sticky, and additional-quota routing.
- [x] 6. Add dashboard schemas, client mutation, account control, status presentation, mocks, and frontend tests.
- [x] 7. Document configuring and interpreting per-account usage limits in the routing guide.
- [x] 8. Run focused backend/frontend tests, migration checks, lint/type checks, and the repository verification suite available in the checkout.
- [x] 9. Address current-head review regressions for precision, error precedence, fair-share eligibility, opportunistic admission errors, and normalized telemetry freshness.
- [x] 10. Revalidate continuity-pinned HTTP bridge turns and quota-planner synthetic warmups against the canonical account usage-limit policy.
- [x] 11. Revalidate each logical request on an existing proxy WebSocket against the pinned account's usage-limit policy.
