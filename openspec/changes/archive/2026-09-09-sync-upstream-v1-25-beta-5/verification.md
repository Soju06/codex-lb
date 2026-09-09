# Beta.5 plus quota-recovery and selection-scope verification

## Candidate and authority

- Fork baseline: `7982375e8a86a5d0c5caf13de0c91458b43c4165`.
- Pinned release: `v1.25.0-beta.5`, peeled commit `b54696466d8e073832b37dd0655a14ca776fc00b`. The annotated tag object is retained as MERGE_HEAD and peels to that commit; HEAD has not advanced.
- Additional runtime fix: #2078, `3d34092d94dcb2f6b66099459b2216b6b7368a28`, applied without committing. Its unrelated service import-format hunk is omitted. The fork quarantine policy is retained.
- Separately approved follow-up: `fix-selection-deadline-error-envelope` carries authenticated hard-owner scope mismatch through typed selection results to the shared recovery helper. The full candidate is beta.5 plus #2078 plus this focused fix, not an untouched upstream tag. The separate 22-Purpose documentation cleanup is verified and archived.
- Six merge conflicts resolved by combining contracts: native imports, generated settings count, settings budget test, outbound client context/spec and Responses spec. The combined settings count is 136, retaining the fork's memory knob and the two beta.5 knobs.
- Python 3.14.7, AnyIO 4.15.1, Bun 1.3.14; frozen Python/frontend dependency installs succeeded.
- Candidate Rust sources, Cargo manifests/lock and Dockerfile match the pinned release. The separately built helper SHA-256 is `62384a87d581514587533b45894ea08602aa50961288d2052731ef9f67f7f58a`.
- No branch, commit, push, PR, production rollout, rollback or production data mutation. Existing untracked `simplify-install-one-liners/` is untouched. Build/test images and synthetic databases are not serving production.

## Completed checks

Counts represent separate, overlapping runs and must not be summed as unique coverage.

The five final PostgreSQL reports contain 92 distinct passing test node IDs;
these include repetitions of SQLite-covered behavior and are not an extra 92
unique application cases on top of the broad backend suite.
Every one of the 34 PostgreSQL-only cases skipped by the SQLite integration run
maps to a passing test in those reports. The final four-case supplement also
checks both strengthened public scope-route cases on PostgreSQL.

| Check | Result |
| --- | --- |
| Hard-owner accepted bridge replay, turn-state and settlement | 73 passed, 1,156 deselected |
| Native adapter/WebSocket capacity/SSE fixtures, settings, overload/error-rate weighting | 98 passed |
| #2078 reproduction before runtime patch | 4 expected regression failures: exhausted accounts incorrectly persisted ACTIVE |
| Routing/proxy/quota unit and integration after #2078 | 1,750 passed |
| Initial full unit/simulation and request-log options, before scope fix | 8,485 passed, 100 skipped, 1 expected failure, 15 warnings; 240.56 seconds |
| Initial full integration core, before scope fix | 2,598 passed, 34 skipped, 1 failed, 63 warnings; 1,101.78 seconds (JUnit) |
| Final full unit/simulation and request-log options | 8,495 passed, 100 skipped, 1 expected failure, 15 warnings; 217.54 seconds (`unit-verified.xml`) |
| Final full integration core | 2,600 passed, 34 PostgreSQL-only skips, zero failures, 62 warnings; 1,024.51 seconds (`integration-final.xml`) |
| Scope/deadline focused regression set | 15 passed; timeout/capacity controls: 28 passed; mutation: 6 expected failures and 3 passing in-scope controls |
| Sticky-session module, candidate, two workers | 41 passed, 1 failed; 86.22 seconds |
| Final expanded sticky-session module after scope fix, two workers | 43 passed; 19.33 seconds (`sticky-sessions-final.xml`) |
| Same module on fork beta.4 baseline, same dependencies, two workers | 41 passed, the same test failed with the same error; 86.19 seconds |
| Exact failing test, isolated on beta.4 / candidate | 1 passed / 1 passed; 77.28 / 77.00 seconds; does not supersede the failing suite results |
| Rust fmt, clippy with warnings denied, workspace tests, release build | Pass; 19 Rust tests |
| Real rebuilt helper through Python SSE/routed boundary | 23 passed |
| Isolated graceful process shutdown | 5 passed |
| Final combined real helper and graceful shutdown, after scope fix | 28 passed; 11.01 seconds (`native-shutdown-final.xml`) |
| PostgreSQL 18 quota/multi-replica and migration contracts | 31 passed, 29 unrelated migration cases deselected; 98.51 seconds |
| PostgreSQL-only integration supplement | 22 passed, 2,634 deselected; 63.74 seconds |
| PostgreSQL quota-selection recovery supplement | 7 passed, 6 deselected; 15.02 seconds |
| PostgreSQL durability/timezone/account-deletion supplement | 34 passed; 72.95 seconds |
| Final PostgreSQL SQL-shape coverage gaps plus scope-route cases | 4 passed; 12.35 seconds (`postgres-final-gap.xml`) |
| Isolated PostgreSQL migration CLI upgrade/check | `20260830_000000_add_quota_warmup_claim_expiry`; policy OK, no schema drift |
| Frontend frozen install, lint, type-check and build | Pass |
| Full frontend suite | 155 files, 1,248 tests passed; 547.10 seconds |
| Built frontend against isolated real backend | 5 browser smoke tests passed |
| Python source distribution and wheel | Rebuilt in `dist-final/`; scope selector/recovery helper and native adapter match candidate source exactly; previous asset inspection retained |
| Ruff lint/format, ty | Pass after scope fix; 1,062 Python files formatted |
| Proxy architecture, timing seams and cancellation safety | Pass after the quota patch |
| Local simplicity budgets | Pass: README 146/200 lines, 9/10 headings; env example 46/60; core nav 5/5; no extra tracked root entries |
| Strict integration and imported hard-owner/overload/quota changes | All four passed |
| Canonical OpenSpec normal validation | 59/59 passed |
| Canonical OpenSpec strict validation | 59/59 passed after the separately approved Purpose cleanup (previously 37 passed, 22 failed) |

The final unit/simulation and full integration reruns are green after the
separately approved scope fix. The original failure is retained as baseline
evidence, not waived by isolated passes. The scope fix preserves exact error-code
and ownership assertions; it does not increase the timeout or accept either
terminal classification. Native-helper and shutdown checks were also repeated
successfully against the final source.

## Environment and exact commands

Reports and the candidate helper are under `/tmp/codex-lb-beta5.h1f5mw`; SQLite tests use `/dev/shm/codex-lb-beta5-tests.JEBC1Z`. Backend commands unset `CODEX_LB_TEST_DATABASE_URL`, exclude ambient bootstrap credentials, set `CODEX_LB_ENV_FILE=/tmp/codex-lb-beta5.h1f5mw/no-env`, and set TMPDIR to the isolated tmpfs path. Final reruns explicitly use `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN=beta5-disposable-test-token`. PostgreSQL-only commands override the test database URL to the newly created loopback-only PostgreSQL 18 test container, never to production.

```sh
uv sync --frozen --group dev
.venv/bin/python -m pytest -q tests/unit/test_proxy_http_bridge.py tests/integration/test_http_responses_bridge.py -k 'accepted or reconnect_turn_state or retired_turn_state or settlement' --timeout=180 --timeout-method=thread --maxfail=3 --tb=short --junitxml=/tmp/codex-lb-beta5.h1f5mw/bridge.xml
.venv/bin/python -m pytest -q tests/unit/test_native_egress.py tests/unit/test_native_websocket_capacity.py tests/unit/test_native_sse_fixtures.py tests/unit/test_settings_reference.py tests/unit/test_error_rate_weighting.py tests/unit/test_overload_backoff.py --timeout=180 --timeout-method=thread --maxfail=3 --tb=short --junitxml=/tmp/codex-lb-beta5.h1f5mw/native-routing.xml
.venv/bin/python -m pytest -q tests/unit/test_load_balancer.py tests/unit/test_proxy_utils.py tests/integration/test_load_balancer_integration.py tests/integration/test_load_balancer_multi_replica.py tests/integration/test_proxy_transient_retry.py --timeout=180 --timeout-method=thread --maxfail=3 --tb=short --junitxml=/tmp/codex-lb-beta5.h1f5mw/quota-after.xml
.venv/bin/python -m pytest -q -n 2 --dist worksteal --timeout=180 --timeout-method=thread --maxfail=5 --tb=short --junitxml=/tmp/codex-lb-beta5.h1f5mw/unit-simulation.xml tests/unit tests/simulation tests/test_request_logs_options_api.py
.venv/bin/python -m pytest -q -n 2 --dist worksteal --timeout=180 --timeout-method=thread --maxfail=5 --tb=short --junitxml=/tmp/codex-lb-beta5.h1f5mw/integration-core.xml tests/integration --ignore=tests/integration/test_native_sse_egress.py --ignore=tests/integration/test_native_routed_egress.py --ignore=tests/integration/test_graceful_websocket_process_shutdown.py
# Final reruns after the separately approved scope fix:
.venv/bin/python -m pytest -q -n 2 --dist worksteal --timeout=180 --timeout-method=thread --maxfail=5 --tb=short --junitxml=/tmp/codex-lb-beta5.h1f5mw/unit-verified.xml tests/unit tests/simulation tests/test_request_logs_options_api.py
.venv/bin/python -m pytest -q -n 2 --dist worksteal --timeout=180 --timeout-method=thread --maxfail=5 --tb=short --junitxml=/tmp/codex-lb-beta5.h1f5mw/integration-final.xml tests/integration --ignore=tests/integration/test_native_sse_egress.py --ignore=tests/integration/test_native_routed_egress.py --ignore=tests/integration/test_graceful_websocket_process_shutdown.py
.venv/bin/python -m pytest -q -n 2 --dist worksteal tests/integration/test_proxy_sticky_sessions.py --timeout=180 --timeout-method=thread --tb=short --junitxml=/tmp/codex-lb-beta5.h1f5mw/sticky-sessions-final.xml
CODEX_LB_NATIVE_EGRESS_TEST_BINARY=/tmp/codex-lb-beta5.h1f5mw/codex-lb-native-egress .venv/bin/python -m pytest -q tests/integration/test_native_sse_egress.py tests/integration/test_native_routed_egress.py tests/integration/test_graceful_websocket_process_shutdown.py --timeout=180 --timeout-method=thread --maxfail=3 --tb=short --junitxml=/tmp/codex-lb-beta5.h1f5mw/native-shutdown-final.xml
.venv/bin/python -m pytest -q -n 2 --dist worksteal tests/integration/test_proxy_sticky_sessions.py --timeout=180 --timeout-method=thread --tb=short --junitxml=/tmp/codex-lb-beta5.h1f5mw/sticky-sessions-rerun.xml
CODEX_LB_NATIVE_EGRESS_TEST_BINARY=/tmp/codex-lb-beta5.h1f5mw/codex-lb-native-egress .venv/bin/python -m pytest -q tests/integration/test_native_sse_egress.py tests/integration/test_native_routed_egress.py --timeout=180 --timeout-method=thread --maxfail=3 --tb=short --junitxml=/tmp/codex-lb-beta5.h1f5mw/native-real.xml
.venv/bin/python -m pytest -q tests/integration/test_graceful_websocket_process_shutdown.py --timeout=180 --timeout-method=thread --maxfail=2 --tb=short --junitxml=/tmp/codex-lb-beta5.h1f5mw/shutdown.xml
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/ty check
.venv/bin/python scripts/check_proxy_architecture.py
.venv/bin/python scripts/check_proxy_timing_seams.py
.venv/bin/python scripts/check_cancellation_safety.py
```

The sticky-session module comparison used a detached temporary worktree at
`7982375e`, the same absolute candidate Python executable, and the same pytest
arguments with report `sticky-sessions-beta4.xml`. Both module runs used an
explicit synthetic `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN=beta5-disposable-test-token`
to avoid logging generated bootstrap credentials. The baseline worktree was
removed after comparison; its source remains available at the pinned commit.
The isolated test selector was
`tests/integration/test_proxy_sticky_sessions.py::test_codex_goal_restart_cannot_retire_owner_outside_api_key_scope`;
the baseline report is `/tmp/codex-lb-beta4-target.xml`, while the isolated
candidate pass is recorded in terminal output only.

Frontend commands run from `frontend/`, with `/tmp/codex-lb-bun-7bRzvv/node_modules/@oven/bun-linux-x64-baseline/bin` prepended to PATH:

```sh
bun install --frozen-lockfile
bun run lint
bun run build
bun run test --maxWorkers=2 --reporter=dot
# From repository root, using the same temporary Bun PATH:
.venv/bin/python scripts/run_dashboard_browser_smoke.py --frontend-built
```

Native validation used the temporary `Dockerfile.native-check` based on the existing Rust build-stage image, then installed missing rustfmt/clippy components, copied only Cargo files/crates and ran `cargo fmt --all -- --check`, `cargo clippy --locked --workspace --all-targets -j 2 -- -D warnings`, `cargo test --locked --workspace -j 2` and the locked release build. The initial attempt failed solely because rustfmt was missing; the subsequent complete run passed. The helper was copied from a stopped export container, which was removed afterward. No HA service or production image tag was recreated.

## Requirements and design review

The verification follows the `openspec-verify-change` completeness, correctness
and coherence dimensions. Current planning status is complete; implementation
completion remains distinct from artifact availability.

| Dimension | Evidence |
| --- | --- |
| Completeness | 11/11 tasks complete; all implementation, broad verification and reporting gates passed; verified and archived |
| Correctness | Seven integration requirements, the quota requirement and the separate scope/deadline requirement map to implementation and regression coverage; all nine canonical blocks match their deltas |
| Coherence | Pinned uncommitted beta.5 merge plus the documented quota and scope fixes, separately scoped Purpose cleanup, preserved fork contracts, no production mutation |

- Hard-owner accepted replay: shared `_affinity_may_resolve_hard_owner`, the bridge exclusion guard and `_http_bridge_reconnect_turn_state` implement the updated contract. Bridge integration exercises capacity codes, 1011/1006, hard-owner selection and unexcluded soft-row movement. Existing accepted-output, anchored/file ownership, sequence and API-key settlement controls remain covered by the broad suites.
- Native buffering: `native_buffer.py` is unchanged; the native adapter retains shared raw/decoded byte budgets, accepted-prefix overflow, owned cancellation tasks, diagnostics and 32-event fairness. Capacity tests exercise 100/300/500 sockets, helper/connection overflow, shutdown and released charges. Real helper tests exercise framed limits, invalid UTF-8, fragmented activity, cancellation isolation, HTTP error/raw-body paths and routed endpoint ownership.
- Overload isolation: `overload_backoff.py` and `sticky_selection.py` cover escalation, disablement, decay, lone-candidate fallback, soft-owner rebinding, cap exemption/spillover and secondary budgets. `error_rate.py` tests cover evidence minimums, expiry, weighted draws/top-k and disabled weighting. Quarantine remains status-based in the fork.
- Quota recovery: original #2078 tests map one account-routing requirement and its 11 scenarios to state building, shared error handling, persistence and two independent replicas. New exhausted samples and stale/same-second credits cannot clear a block; valid newer available evidence still recovers it. The before/after regression distinguishes the actual fix from already-passing controls.
- Canonical specs now include the imported deltas and integration buffer contract. The earlier accepted-replay paragraph/scenarios were reconciled so they no longer demand cross-account movement for a hard owner. Account-routing context was corrected to describe the preserved fork quarantine rather than upstream's unexpired-token policy. Narratives live in context documents, not normative spec text.
- No Alembic file, HA rollout script, native buffer implementation or frontend component changed. The only frontend source metadata change is the release version. There is no new UI layout requiring before/after screenshots in this candidate.

All nine delta requirement blocks (seven integration-owned blocks, plus the
quota-recovery and scope/deadline blocks) were compared with the canonical specs
and match exactly. The integration has 41 scenarios, the quota delta adds 11,
and the scope/deadline fix adds four. The imported SSE
contracts retain their existing upstream owners. The full unit report also
confirms 30 HA workflow cases, two HA-default cases, four proxy-assignment cases,
11 key-installer cases and nine native WebSocket capacity cases passed; those
counts are already included in the initial 8,485 total.

## Known limitations and archive gate

**Original integration blocker — resolved; final broad rerun green.**
`tests/integration/test_proxy_sticky_sessions.py`
(`test_codex_goal_restart_cannot_retire_owner_outside_api_key_scope`) expects the
hard-owner failure envelope but receives `upstream_request_timeout`. The broad
integration report contains this one failure. Both candidate and beta.4 module
reruns reproduced it with two workers; both isolated exact-test runs passed. At
that initial comparison, the streaming retry loop, budgeted selector and test
were unchanged from baseline.
The failing logs on both trees show `Stream account selection exceeded request
budget` with deadline cancellation during the final selection after repeated
hard-owner-unavailable results. This is evidence of a baseline source issue
under the candidate dependency environment, not a beta.5-only source regression
and not proof that production's older dependency environment behaves identically.
The API-key scope remains fail-closed before dispatch; the failure is in which
terminal error is surfaced. The original result is preserved as evidence.

The approved `fix-selection-deadline-error-envelope` change now skips capacity
recovery for a resolved out-of-scope hard owner. Its virtual, route, bridge and
WebSocket tests preserve scope, leases, durable ownership and the exact terminal
code. The first fixed sticky-session module run passed all 42 cases; the public
regression then gained a second owner-status case. The final expanded module
passes all 43 cases and is also part of the full integration rerun. See that
change's verification.md for the mutation test and genuine-timeout/recoverable-
capacity controls. The final broad integration run passes all 2,600 applicable
cases with no failures, including both strengthened public route cases.

**Original strict OpenSpec blocker — resolved and verified.**
The separate `fill-canonical-spec-purpose-sections` change replaced all 22
baseline-identical Purpose placeholders with summaries grounded in the existing
requirements. Normal and strict canonical validation both pass 59/59 with
CI-pinned OpenSpec 1.11.0. No normative requirement was relaxed by that cleanup;
its scope comparison and verification are archived under
`archive/2026-09-09-fill-canonical-spec-purpose-sections/`.

No critical implementation, coverage or coherence finding remains for the local
integration. All nine requirement blocks are synced. The final unit/simulation,
integration, real-helper, static and strict OpenSpec checks pass. Archive is
complete under the repository's default workflow; this does not claim cloud PR
readiness or authorize a production rollout. The focused selection follow-up was
verified and archived separately as `2026-09-09-fix-selection-deadline-error-envelope`.
Imported upstream change folders and unrelated installer work remain untouched.

Unit skips comprise 94 optional Helm-rendering cases (Helm unavailable), three obsolete locking cases, two ambient env-file-discovery cases and one opposite-version CPython canary. The expected lifecycle failure records redundant reservation-release calls that database compare-and-set makes no-ops, not double charging. Warnings include unawaited AsyncMock, fork/deprecation and SQLite index-reflection messages; passing assertions do not make those warnings disappear.

The passing frontend run emitted React `act(...)`, jsdom scroll/canvas/SVG/chart
warnings and MSW unmatched-handler messages for installer/runtime-address mock
requests. No frontend assertion failed; these test-harness warnings remain
visible and were not silenced or repaired through unrelated UI changes.

The first PostgreSQL supplement passed 25 cases and failed one pre-existing fixture before selection because its wrong-unit timestamp exceeded PostgreSQL INTEGER range. That fixture exists unchanged in baseline HEAD. It now uses a still-implausible but representable deadline; the focused SQLite rerun passes and pure unit cases retain the arbitrary-size timestamps. The complete PostgreSQL rerun passed all 26 multi-replica cases plus five PostgreSQL migration cases. The exact pytest selection was `tests/integration/test_load_balancer_multi_replica.py tests/integration/test_migrations.py -k 'not test_migrations or postgresql'`, with the same timeout options and `quota-postgres-final.xml` report.

Post-beta.5 #2173/#2143 are not imported wholesale. The fork retains its tested buffer/fairness policy, but its HTTP event queue remains unbounded. An HTTP-only memory-budget change is still follow-up work; this release does not claim to fix that risk. Current-head cloud CI/CodeRabbit/PR merge gates have not been checked because no candidate commit or PR was requested.

## Later production prerequisites

The final four-case PostgreSQL supplement used a new isolated PostgreSQL 18
container bound only to `127.0.0.1:32796`, with a synthetic database on tmpfs.
It selected `test_additional_latest_by_account_postgres_uses_top1_probes`,
`test_list_quota_keys_postgres_loose_scan_matches_distinct` from
`tests/integration/test_usage_repository.py` and both parameters of
`test_codex_goal_restart_cannot_retire_owner_outside_api_key_scope` from
`tests/integration/test_proxy_sticky_sessions.py`. The same timeout/environment
isolation applied, with `postgres-final-gap.xml` as the JUnit report. That
container was stopped and auto-removed after all four cases passed.

The disposable PostgreSQL test container was stopped and auto-removed after its
checks. Only synthetic tmpfs data was discarded; reports and the candidate helper
remain available in the temporary evidence directory.

Production remains beta.4. A later operator-requested deploy must use the HA deploy skill and `./scripts/deploy-compose-ha.sh deploy`, with fresh topology/readiness, memory and database headroom checks. Build Python and the SSE-capable helper together. No database schema migration is added, but old replicas can still execute the pre-#2078 early-recovery policy during mixed-version overlap; verify that every serving replica reaches the candidate. Overload isolation is replica-local and does not itself provide cross-replica quarantine. Monitor hard-affinity waits, stream completion, reservation settlement, overload isolation and quota oscillation after rollout. Neither rollout nor rollback is authorized by this local verification.
