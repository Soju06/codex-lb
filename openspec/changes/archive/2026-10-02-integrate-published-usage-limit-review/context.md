# Published/local usage-limit integration

The published head adds independent default, 5-hour, and weekly reserve controls. Integration retains those controls and both commit histories, together with the local final-dispatch and sticky-persistence fences. No new service, configuration, or dependency is introduced.

## Contract decisions

The published policy-denial contract is HTTP 429 / `rate_limit_error`, without an upstream reset deadline. Direct Responses requests use the canonical selection-error mapper, preserving the local correction to the earlier manual 502 response. Infrastructure authorization failures remain HTTP 503 and do not retire a healthy bridge. The earlier scalar-review archive records the contract at that earlier head; this integration supersedes its 503 policy-denial wording.

Saved thresholds are updated atomically: omitted values retain current database values, explicit null clears a field, and enabling requires at least one saved or supplied threshold. Published override-only policies and 422 validation remain supported. An empty successful poll supersedes old standard measurements with unavailable placeholders; enabled policies invalidate selection immediately, while disabled policies retain their existing routing semantics.

For example, an enabled weekly-only override that changes while a bridge turn waits for dispatch must reject the unsent turn with `account_usage_limit_reached`, while overlapping dispatched work remains with its original owner and settles normally. A failed authorization read instead returns `account_usage_limit_authorization_failed` and leaves a healthy transport available for retry.

## Architecture and history

One typed fresh authorization snapshot replaces the obsolete cached owner-policy reader. Canonical routing-pool evidence stays separate from eligible capacity, preserving fallback and error precedence without counting blocked accounts. Local injected clocks, scheduler ownership, and the final sticky generation fence remain in place. Equivalent tests are consolidated only where the published suite covers the same product path.

The original scalar revision parent remains valid. A merge revision joins the published override head to the existing local/upstream merge. Upgrade tests build actual schemas from both parent declarations, preserve enabled and disabled saved thresholds, and verify one canonical head without schema drift.

## Verification

Combined bridge/WebSocket/retry/cancellation coverage passed 1,596 tests. Contract tests passed 176 cases and the final admission-boundary slice passed 12. The broader core suite passed 1,223 tests with 27 skips; its only failure was an older assertion expecting the throttled header refresh to finish when immediate selection invalidation completed. The corrected test passed, and the complete direct Responses/live-ingest/topology rerun passed 143 tests with four PostgreSQL-specific skips. The additional warmup/routing/dashboard slice passed 314 tests with eight PostgreSQL skips; its old SSE error-type expectation was corrected and verified in the complete Responses rerun.

Evaluator/typed authorization coverage passed 87 tests. Historical upgrade coverage passed three local/upstream starting revisions and a database built from the published graph, retaining saved defaults and overrides with no schema drift. `make migration-check` reports one head, policy OK, and no schema drift. Repository lint, architecture/cancellation/timing/settings checks, backend typing, frontend lint/types/production build, 222 focused account UI tests, and 371 dashboard/mock tests passed. Strict validation passed both changes and all 67 main specs.

PostgreSQL is not configured in this checkout; current SQLite checks do not substitute for PostgreSQL CI. The previous published audit's PostgreSQL evidence remains historical. No claim is made that current-head cloud CI has completed.

## Cloud security scan follow-up

The first integrated head passed cloud PostgreSQL migration checks, but Docker's Trivy scan rejected inherited urllib3 2.7.0 for CVE-2026-97687 and CVE-2026-97689. Both are fixed in 2.8.0. Raise the existing dependency floor and update only urllib3 in the lock; retain the security scan unchanged. This is a patch to an existing runtime dependency, with no new feature, setting, service, or dependency.

Only urllib3 changed in the lock, to 2.8.0; the project version spelling and all other dependency versions remain intact. `uv lock --check` passed, the installed version is 2.8.0, package compatibility checks passed for 103 installed packages, and 36 metrics/usage-client tests passed. The broader optional telemetry check exposed three isolated lifespan fixture failures, tracked for the independent review rather than attributed to urllib3.

## Delivery

The integrated merge was pushed normally as `ab7794b57` and verified as PR #1528's published head before the requested independent GPT-6.1 Sol max review started. Both histories and upstream `f8ffbac20` are ancestors. The dependency scan correction follows in a focused commit on the same PR; current-head cloud checks must be evaluated again after that push.
