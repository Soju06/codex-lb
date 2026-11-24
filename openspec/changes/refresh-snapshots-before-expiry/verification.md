# Verification

## Completed checks

- The complete SQLite-selected Python suite exited successfully with **13,910
  passed, 515 skipped and one expected failure**. JUnit records contain no failures
  or errors, and the supervisor found no remaining test processes. Backend- and
  runtime-specific opt-in tests retain their documented skips.
- The snapshot integration module passed 29 tests. A targeted set of 39 consumer,
  smoke-harness, environment and migration-checker tests also passed.
- All 1,667 frontend tests across 184 files passed, as did frontend typing, lint
  and the production build. The frontend source was unchanged during the final
  Python confirmation.
- Full Python lint, formatting and Git-aware type checking passed. Migration
  topology passed against the current upstream parent. Strict validation of this
  change passed; the 67 main specifications passed normal validation.
- Seven focused scheduling cases use the actual repository, scheduler and health
  route. Both the old and early schedules service their due work. Four bounded
  collectors cross the old freshness boundary while collection is still active;
  the early schedule finishes before that boundary. Both eventually publish a
  replacement. A noon clock anchor avoids an unrelated midnight rollover.
- A separate MariaDB 10.11 check exercised old and corrected publication schedules,
  due selection, successful replacement, failure backoff, stale values and
  generation/owner fencing.
- An isolated real smoke backend published its four initial snapshots and passed
  analytical readiness before shutdown. The harness retains local snapshot
  production while disabling outbound background loops. Its regression exercises
  an HTTP 503 exception before readiness succeeds.
- Source review found and corrected the idle-producer negative control and
  snapshot-unaware consumer fixtures. Follow-up review found no remaining concrete
  issue in the reviewed scope.

## Limits and retained failures

The scheduling tests substitute collectors and advance a controlled clock. They
prove scheduling and readiness transitions, not real collector latency or sustained
performance. The SQLite-selected suite and focused MariaDB check are not a complete
cross-engine or native-runtime acceptance claim. This follow-up adds no new product
speed claim.

Earlier incomplete and failing runs remain in the private evidence. They exposed
missing Git metadata and copied dependency discovery in the test environment,
consumer-fixture mismatches, and a run that reached 100% before its runtime limit
but did not return a final result. None of those runs is counted as the successful
confirmation above. Repository-wide strict OpenSpec mode still reports pre-existing
long-requirement warnings; the unchanged main-spec tree was not rewritten or
represented as strict-clean.

Deployment acceptance is recorded separately. Source review, an installed-but-stopped
candidate, or component checks alone do not establish safe adoption or sustained
freshness. Operational evidence remains outside the source tree. GitHub CI, current
review findings and maintainer approval remain separate merge gates.
