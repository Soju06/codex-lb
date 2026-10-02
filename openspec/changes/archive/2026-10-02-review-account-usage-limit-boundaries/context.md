# Local review of per-account usage limits

The review started at `7c3db8159` on `feat/per-account-usage-limits`, against
merge base `d4b00fd05`. The user selected this local branch and requested local
fixes with commits, then requested synchronization with the newest upstream.
Upstream `f8ffbac20` was integrated by merge commit `f81ecde84`; no local history
was rebased and no remote branch or PR review thread was changed.

## Correctness findings and fixes

All four findings below were reproduced before their fixes. The initial six
regression cases failed at the externally affected paths, then passed. Coverage
was expanded to prewarm and recovery-alias cleanup after the fixes.

| Priority | Finding | Fix and evidence |
| --- | --- | --- |
| P1 | An HTTP bridge turn passed its last policy check before waiting for serialized dispatch or durable preparation. A cap change during either wait could still send the turn. | `request_submit.py:2190` authorizes after preparation and `:2559` does the same for prewarm. `:2237` rolls back an unsent recovery alias on policy denial as well as cancellation. Route test `test_http_bridge_policy_change_during_dispatch_wait_rejects_unsent_turn`, prewarm test `test_prewarm_policy_change_during_dispatch_wait_rejects_unsent_work`, and three recovery-alias interruption cases verify no send, predecessor ownership, and cleanup. |
| P1 | Selection invalidated while affinity was persisted could still return the account and its provisional lease. | `sticky_selection.py:1447` fences the completed admission, releases the lease, and returns `selection_state_changed`. `test_sticky_persistence_policy_change_releases_admission` verifies the public result, preserved persisted affinity, and zero pressure. |
| P1 | A successful poll with omitted or empty standard windows left older below-limit telemetry usable by capped accounts. | `usage/updater.py:730` and `:748` write existing unavailable placeholders for all standard slots only for enabled policies. Four `test_empty_poll_supersedes_usage_limit_telemetry` cases cover ordinary and monthly accounts, dashboard state, and streaming/nonstreaming Responses rejection. Uncapped additional-only behavior remains covered by updater tests. |
| P2 | Direct HTTP Responses selection converted a local policy denial into HTTP 502, while bridge admission used HTTP 503. | `streaming/retry.py:1644` uses the canonical selection envelope. Streaming and nonstreaming `/v1/responses` return 503 with `account_usage_limit_reached` and `server_error`; backend SSE retains its terminal policy event and upstream exhaustion retains its existing contract. |

Paths in this table are relative to `app/modules/proxy/_service/http_bridge/`,
`app/modules/proxy/_load_balancer/`, `app/modules/`, or
`app/modules/proxy/_service/` as applicable. Tests live under `tests/unit` and
`tests/integration`.

## Upstream reconciliation

The branch was 362 upstream commits behind when synchronization began. Textual
conflicts were reconciled across routing, bridge/WebSocket admission, warmup,
frontend components, tests, and specs. Automatic merges were also checked:
the usage-limit route now uses `Permission.ACCOUNTS_WRITE` and records the
authenticated audit actor and account target; its permission is included in
the dashboard route matrix. Policy reads use the owner's injected clock and
scheduler, and usage freshness follows upstream's fixed refresh policy.
Reauthentication warnings preserve usable-token routing. Standard policy rows
remain available when request ranking uses additional quotas. Warmup cleanup
preserves upstream's claim timestamp and lease-expiry fence, stable request
identity, and cancellation semantics.

`20261002_000000_merge_usage_limits_and_main_heads` joins the existing
usage-limit and upstream migration heads without changing either published
revision's identifier or parent. Tests upgrade databases from both heads and
check default-disabled policies and preservation of enabled and saved policies.
The topology guard accepts this repair only when a merge descends from the
branch revision and every upstream head. It still rejects an incomplete merge.

## Architecture and bloat assessment

The shared pure evaluator in `app/core/usage/account_limits.py` is the right
boundary: routing, account summaries, and warmups share shape, freshness, and
threshold semantics. Stored configuration is two scalar fields; the displayed
state is derived. Standard observations retained alongside request-specific
additional quotas are necessary authorization evidence, not a second policy
source. Existing selection generations, invalidation signals, unavailable
placeholders, error envelopes, and admission cleanup are reused. The feature
adds no settings, dependencies, background service, or dashboard navigation.
Policies default off.

The largest risk is the number of authorization entry points and asynchronous
boundaries. A single early check cannot govern work that waits or changes
ownership. Boundary checks and product-path race tests are justified; replacing
them with a separate policy cache or independent policy service would introduce
more invalidation and ownership problems. Bridge reads are deadline-bounded and
never hold the pending-response lock. Their final check holds dispatch
serialization while older responses continue to settle independently.

Demonstrated redundant work was removed: the duplicate selector call for an
empty policy-eligible pool, defensive attribute access on typed warmup usage
rows, a memoized runtime-account map, and a duplicate ORM row copier. Usage
snapshot/owner authorization moved to
`app/modules/proxy/_load_balancer/usage_limits.py`, keeping the existing balancer
size limit instead of raising it. Warmup cancellation cleanup uses upstream's
shared waiter while retaining cancellation precedence when cleanup fails.

After integration, before the final review-document sync, the feature diff
against upstream was 113 files, +8,261/-559 lines: backend 34 files
+1,762/-390, frontend 28 files +969/-11, tests 34 files +4,484/-148, OpenSpec
15 files +1,022/-10, and two other files +24/-0. Most added lines are tests and
specs, but this still exceeds the repository's approximately 800-net-line PR
review budget. A publishing pass should split reviewable layers (policy schema
and evaluation, enforcement paths, dashboard controls) with explicit
dependencies and a complete enforcement layer before release. This local task
retains the user-selected branch; it does not create or publish a PR stack.

## Operational limits

Limits constrain new work using observed usage. A 10% cap rejects a turn when
current telemetry reaches 10%; upstream reporting lag and work already sent can
overshoot. Missing or stale evidence blocks an enabled policy. Cross-replica
visibility follows the existing invalidation/cache mechanisms; this feature
does not reserve an exact amount of upstream quota.

PostgreSQL transaction-lock, query-plan, and advisory-lock cases require an
external test server. None was configured or reachable locally, and the Docker
daemon was unavailable. SQLite migration and policy-data checks do not verify
those PostgreSQL-specific behaviors. GitHub CI and current-head review gates
were not run for local commits. PR #1528 remains linked to this thread and its
published head is separate from this local branch.

## Verification

Checks ran against the merged implementation. Test-fixture corrections were
rerun at their affected surfaces; overlapping batches are not added together.

| Check | Result |
| --- | --- |
| Routing, usage refresh, Responses HTTP/WebSocket, live ingest, usage repository, quota-planner API, and atomic warmup | 872 passed; 21 PostgreSQL-only cases skipped. After final clock plumbing, the affected balancer, virtual-clock, and evaluator files were rerun: 359 passed. |
| Public selection, idle bridge leases, and extended accounts API | 144 passed after the final admission changes. |
| Quota planner, dashboard permission matrix, and accounts API compatibility | 129 passed. |
| Bridge, proxy/WebSocket transport, warmup, and cancellation coverage | 2,717 passed; one upstream warmup test omitted its persisted account. The fixture was corrected, then both warmup cases in `test_proxy_utils.py` passed. This was a focused rerun, not a fresh full batch. |
| Review boundary regressions | 10 passed, including three recovery-alias interruption cases and four empty-poll cases. |
| Migration topology and integration | 77 passed; 9 PostgreSQL-only cases skipped. Includes upgrades from both former heads, policy-data preservation, and usage-limit upgrade/downgrade coverage. |
| Fresh SQLite migration and schema check | Canonical head `20261002_000000_merge_usage_limits_and_main_heads`; `migration_policy=ok`; `schema_drift=none`. |
| Account/dashboard frontend | The 19-file batch had 195 passing cases and four failures from a missing `usageLimitMutation` page mock. After correcting the mock, all 9 page cases passed: 199 distinct cases verified across the batch and focused rerun. The separate account usage-limit integration flow passed all 6 cases. |
| Static checks | `make lint`, backend `ty`, frontend lint, typecheck, and production build passed. The final warmup fixture passed focused Ruff lint/format checks. |
| OpenSpec | All 67 main specs and the review change passed strict validation; delta requirements were synchronized into the owning main specs before archive. |

Backend checks used the project virtualenv and a temporary encryption key via
`CODEX_LB_ENCRYPTION_KEY_FILE`, avoiding a write to the default system key path.
No PostgreSQL service or remote CI result is implied by these local checks.

### Requirement and scenario verification

| Requirement | Implementation and scenario evidence |
| --- | --- |
| Bridge authorization follows dispatch preparation | `app/modules/proxy/_service/http_bridge/request_submit.py:2190` and `:2559`; dispatch-wait route regression in `tests/integration/test_http_responses_bridge.py:4967`, prewarm regression in `tests/unit/test_proxy_http_bridge.py:35153`, and durable-preparation/recovery rollback cases at `:26000`. Policy reads assert the pending lock is not held. |
| Sticky admission observes invalidation during persistence | `app/modules/proxy/_load_balancer/sticky_selection.py:1447`; public selection and released-lease regression in `tests/unit/test_load_balancer_contract.py:1544`. |
| Empty successful polls supersede capped-account measurements | `app/modules/usage/updater.py:730`; ordinary/monthly, omitted/empty, dashboard, and Responses coverage in `tests/integration/test_accounts_api_extended.py:103`. Disabled null-window and additional-only behavior remain covered in `tests/unit/test_usage_updater.py:1025` and `:4019`. |
| Responses denials use the canonical envelope | `app/modules/proxy/_service/streaming/retry.py:1644`; both direct HTTP modes in the empty-poll cases, bridge HTTP denial in the dispatch regression, and backend terminal SSE in `tests/integration/test_proxy_responses.py:1061`. |
| Usage-limit migrations preserve parallel upgrade paths | The explicit two-parent merge revision and `tests/integration/test_migrations.py:723` cover both previous heads, preserved enabled/saved values, default-disabled new fields, and the canonical head. |
| Topology validation accepts only converged parallel history | `scripts/check_migration_topology.py:543`; full and incomplete convergence cases in `tests/unit/test_check_migration_topology.py:404`. |

Completeness: all six delta requirements and eleven scenarios have
implementation and test evidence. Correctness: the four reproduced findings
are fixed, and the affected checks pass after integration. Coherence: the
implementation follows the design's existing evaluator, generation fences,
unavailable placeholders, canonical errors, ownership cleanup, and forward-only
history reconciliation. No critical implementation finding remains from this
review. PostgreSQL verification and the publishing-size concern remain the
documented limits above.
