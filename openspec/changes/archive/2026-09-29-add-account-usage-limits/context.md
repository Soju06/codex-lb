# Local audit

The September 29 audit reviews local head `41ec18a31` against its local main
ancestor `d4b00fd05`. The published PR has diverged; this work preserves the
checked-out scalar policy and leaves rebasing to a separate step.

Enabled policies need immediate selection invalidation after live observations.
Uncapped accounts still need the existing throttled invalidation for ranking
and status recovery. Removing that path changes behavior when the feature is off.

Authorization before admission cannot authorize dispatch after a wait. For
example, a socket can pass a 10% cap check, wait for a response-create slot,
then send after another turn raises observed usage to 12%. Dispatch must
recheck the policy and pending-request ownership, while already-sent turns
retain their settlement paths. Database failures must reject only the new turn.

Bridge policy reads must not hold the lock that the upstream reader needs to
complete other turns. This is separate from lease reacquisition, which still
needs atomic session accounting.

The policy remains observation-bound: upstream reporting and already-dispatched
work can overshoot the configured threshold. No new settings or dependencies
are needed for these fixes.

Both dashboard toggle directions omit the percentage. For example, a tab that
loaded a disabled 10% limit must enable the latest saved 20% limit after another
client edits it, rather than silently restoring 10%. A conditional database
update prevents enabling a removed policy; this conflict returns 409 so the
operator can reload or explicitly configure a new value.

## Verification

The audit covers persistence and migration constraints, API validation and stale
toggles, normalized quota evaluation, routing strategies and error precedence,
fair-share accounting, shared-transport ownership and settlement, live telemetry,
synthetic warmups, and dashboard controls. The implementation follows the shared
evaluator and existing invalidation mechanisms; no new settings or dependencies
were introduced. The advisory-usage requirement now explicitly scopes its
unchanged behavior to accounts without an enabled blocking usage policy.

| Check | Local result |
| --- | --- |
| Full unit suite, request-log options, cancellation/drain e2e slice | 6,447 passed and 71 skipped; seven incomplete probe fixtures were fixed, and all seven passed with `pytest --lf` (the complete probe file also passed all 13 tests). |
| HTTP bridge integration, idle leases, proxy utilities | 1,277 passed. |
| Account API, migrations, public responses, warmup/planner, live telemetry, usage repository integration | 235 passed and 20 PostgreSQL-only skips. |
| Final WebSocket and account-probe integration | 153 passed; one aiosqlite worker warned that its event loop was closed. The affected overlap test's nine cases then passed with thread warnings treated as errors. |
| Full frontend suite on the original installed dependencies | 1,172 passed. |
| Full frontend suite after a frozen-lockfile install | 1,168 passed in the four-worker run; four UI waits failed under concurrent load. All four affected files passed their 18 tests with the repository's default sequential setting. |
| Frontend lint, typecheck, production build on frozen dependencies | Passed. |
| `make migration-check` | Scratch SQLite upgrade reached the single intended head; migration policy passed and schema drift was absent. |
| `make lint`, `uv run ty check`, strict change validation, main-spec validation | Passed. |

Regression evidence includes the public bridge and WebSocket overlap tests,
deadline/cancellation authorization tests, live-ingest cache/precision tests,
load-balancer snapshot-invalidation and exhaustion-contract tests, account API
stale-toggle/removal conflicts, and the dashboard stale-tab and shared-mock flows.

PostgreSQL execution remains unverified: no test URL, reachable PostgreSQL server,
or running Docker daemon was available. Helm-dependent unit cases were skipped
because Helm was unavailable. The collaborative browser could not reach the
isolated scratch server (`ERR_CONNECTION_REFUSED`), so no visual screenshot
verification is claimed. That server was stopped and its temporary database
removed. Cloud CI, CodeRabbit, merge gates, and today's upstream rebase were
outside this local audit.
