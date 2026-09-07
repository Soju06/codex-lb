# Beta.4 integration verification

## Candidate and authority

- Source fork: `2930dec06bf9a76a5c29c6d15eb7c6311407dbfc`.
- Target upstream: `v1.25.0-beta.4`, peeled `15ccd901bf013daa11c67934cc019a9b33f2f72b`.
- Integration: non-committing merge, with the target retained as `MERGE_HEAD`; four textual conflicts resolved and the shared diagnostic helper moved to avoid a bridge/websocket import cycle.
- No branch, commit, push, PR, production deploy, rollback, or production data mutation. Unrelated `simplify-install-one-liners/` is preserved byte-for-byte.
- Python actually used: CPython 3.14.7, AnyIO 4.15.1; frontend Bun 1.3.14. Dependency installs used frozen lockfiles.

## Completed checks

Counts below are separate runs and overlap; do not sum them as unique coverage.

| Check | Result |
| --- | --- |
| Application import | Pass |
| Ruff lint/format, ty | Pass; 1,056 Python files formatted |
| Proxy architecture, timing, cancellation safety | Pass, including beta.4's direct-await gate |
| Full unit/simulation and request-log options suite | 8,380 passed, 100 skipped, 1 expected failure, 15 warnings; 218.21 seconds |
| Full integration except separately run process-shutdown file | 2,573 passed, 34 PostgreSQL-only skips, 64 warnings; 716.39 seconds |
| Focused AnyIO/eligibility/proxy-policy/transport/cancellation tests | 136 passed |
| Reproduced merge failures plus optional-metadata regression | 7 passed after fixes |
| Reauth/quarantine/bridge/balancer regressions | 62 passed |
| UTC+7, assignment, HA, search paths, installers, native queue tests | 241 passed |
| Native egress and byte-capacity suites | 30 passed |
| Accepted retry, owner, settlement, sequencing and cancellation integration | 71 passed; aiosqlite late-thread warning recorded |
| Fork API routes: API keys, import/OAuth, search/control, settings | 537 passed |
| Key-dashboard APIs | 10 passed |
| Isolated dashboard bootstrap and injected-clock regressions | 12 passed |
| Frontend lint/type/build | Pass |
| Full frontend suite | 155 files, 1,248 tests passed |
| Focused route/key/import/proxy-warning UI | 32 passed |
| Built frontend with real local backend | 5 browser smoke tests passed; clean beta.4 shutdown |
| Before/after route screenshots | Captured; key entry made zero administrator API requests |
| Rust release build, fmt, clippy, workspace tests | Pass, 12 tests; beta.3 and beta.4 Rust sources, manifest, lock and Dockerfile are identical |
| Real native helper through Python boundary | 3 passed |
| Isolated PostgreSQL 18 migration upgrade/check | Current revision `20260830_000000_add_quota_warmup_claim_expiry`; no drift |
| PostgreSQL migration/concurrency contracts | 7 passed in final PostgreSQL-only selection; 42 unrelated cases deselected |
| PostgreSQL account-deletion and live-usage integration | 40 passed, including all seven PostgreSQL-only locking/query-plan cases |
| PostgreSQL commit durability, session timezone and usage query contracts | 20 passed, 23 unrelated cases deselected; 29.36 seconds |
| Process shutdown integration, isolated | 5 passed |
| Python source distribution and wheel | Built; wheel assets verified, including key dashboard and accepted replay |
| Canonical OpenSpec validation | 59/59 passed in normal mode |
| Strict integration and imported beta.4 changes | All three passed |
| Strict touched canonical specs | Proxy architecture, key dashboard, frontend architecture, outbound clients and Responses compatibility passed |

Exact representative commands:

```sh
uv sync --dev --frozen
bun install --frozen-lockfile
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/ty check
.venv/bin/python scripts/check_proxy_architecture.py
.venv/bin/python scripts/check_proxy_timing_seams.py
.venv/bin/python scripts/check_cancellation_safety.py
bun run test --maxWorkers=2
bun run lint
bun run build
.venv/bin/python scripts/run_dashboard_browser_smoke.py --frontend-built
npx --yes @fission-ai/openspec@1.11.0 validate --specs
npx --yes @fission-ai/openspec@1.11.0 validate sync-upstream-v1-25-beta-4 --strict
```

Frontend commands run from `frontend/`; the temporary Bun executable was added to PATH. Backend tests explicitly unset ambient `CODEX_LB_TEST_DATABASE_URL`, except the PostgreSQL checks targeting only the new loopback test container. Slow disk-backed SQLite checks were rerun with a fresh `TMPDIR` on tmpfs, without reducing assertions or increasing timeouts. The final broad integration run additionally sets `CODEX_LB_ENV_FILE` to a nonexistent path in its temporary directory and unsets the manual bootstrap-token variable: deleting an environment variable alone still lets Settings discover the operator's `.env.local`. No operator env file or credential was modified. PostgreSQL test storage was tmpfs; its test container was stopped after all supplementary checks.

Full unit/simulation command (with a fresh tmpfs `TMPDIR`):

```sh
env -u CODEX_LB_TEST_DATABASE_URL .venv/bin/python -m pytest -q -n 2 --dist worksteal \
  --timeout=180 --timeout-method=thread --maxfail=5 --tb=short --durations=15 \
  --junitxml=/tmp/codex-lb-beta4-retarget.fy3R0Z/unit-simulation.xml \
  tests/unit tests/simulation tests/test_request_logs_options_api.py
```

The 100 skips are 94 optional Helm-rendering cases (Helm unavailable; chart files unchanged), three obsolete per-account locking cases, two env-discovery cases requiring a checkout without env files, and the opposite-version CPython canary. The one expected failure records redundant reservation-release calls already rendered no-ops by the database compare-and-set; it does not indicate double charging. Existing AsyncMock/fork/deprecation/index-reflection warnings remain visible.

The integration core ran with `-n 3 --dist worksteal --timeout=180 --timeout-method=thread --maxfail=5 --tb=short --durations=15`, `tests/integration --ignore=tests/integration/test_graceful_websocket_process_shutdown.py`, an isolated tmpfs `TMPDIR`/env file, and `CODEX_LB_NATIVE_EGRESS_TEST_BINARY=/tmp/codex-lb-beta3-native.X7pK7P/codex-lb-native-egress`. Its JUnit report is `/tmp/codex-lb-beta4-retarget.fy3R0Z/integration-isolated.xml`.

All 34 PostgreSQL-only skips were exercised successfully in supplementary runs: seven account-deletion/live-usage cases, seven migration/serialization cases, and 20 commit-durability/timezone/usage-query cases. Together with 2,573 SQLite/native integration passes and five isolated process-shutdown passes, this covers all 2,612 integration cases. The 40-case PostgreSQL account-deletion/live-usage run also repeats 33 SQLite-covered cases; do not count those twice. PostgreSQL runs were serial against a disposable PostgreSQL 18 database with only synthetic test data.

## Requirement coverage and coherence

- AnyIO release/cancel/acquire: dependency floor in `pyproject.toml`, locked AnyIO 4.15.1, and `tests/unit/test_anyio_lock_cancelled_waiter_release.py` cover Lock, Semaphore and stdlib reference. Simulation exercises the lifecycle interleavings separately.
- Standalone key auth and route recovery: `frontend/src/App.tsx` keeps key entry outside `AuthGate`; `route-recovery-flow.test.tsx` checks recovery/navigation and `key-dashboard-flow.test.tsx` checks password-independent key access without administrator requests. See [screenshots and capture conditions](evidence/context.md).
- Retained quarantine: eligibility accepts the upstream clock argument but remains status-based; reauth exclusion/reuse tests pass. `tests/unit/test_load_balancer_virtual_clock.py` was adapted to the canonical account-routing contract: both expired and unexpired reauth-required accounts remain quarantined before and after clock advancement, while an active candidate prevents the all-reauth error. Wall-clock access remains forbidden in the test.
- Retained diagnostics: both bridge and websocket import the helper from shared support; absent metadata remains tolerated and specific existing overrides survive. The import graph passes architecture validation.
- Native shutdown test now accepts either the explicit shutdown transport error or the specific helper-EOF protocol error, both valid event orderings, while retaining bounded wakeup, pump completion and capacity/cancel-task cleanup assertions. Runtime behavior was not changed for this test.
- Imported beta.4 retry and proxy-policy requirements were synced from their owning upstream deltas; imported changes remain active and were not mass-archived. No new setting or navigation entry was added by this integration layer. HA rollout scripts were not changed.

## Remaining checks and known limitations

Full unit/simulation and integration verification runs have completed successfully. The integration core passed 2,573 tests with 34 PostgreSQL-only skips; all five process-shutdown integration tests passed in the separate run and all 34 skipped cases passed on PostgreSQL. PostgreSQL supplementary coverage is recorded above and is not included in the SQLite pass count. The final integration run emitted 64 warnings: deprecations, known SQLite index-reflection limitations and one aiosqlite late-thread/closed-event-loop warning attributed during `test_realtime_call_location_drives_supported_account_bound_sideband_routes[frameless]`. The last warning is an unresolved test-harness cleanup concern, not an assertion failure; its emitting test is not proven to be the owner of the leaked thread.

Initial broad runs exposed the fixed metadata/test-fixture issues, a SQLite commit timeout, and one native-shutdown assertion-order issue; they are not recorded as passing runs. One complete unit run had 8,379 passes and one incompatible upstream quarantine expectation; after aligning that test with the retained fork contract, the complete rerun passed. An integration run stopped after five bootstrap failures caused by ambient env-file discovery (1,543 passes, 15 skips before interruption); the isolated bootstrap rerun passed all cases. A previous integration worker group ended with SIGTERM before producing a final report; isolated process-shutdown tests and the remaining full integration suite subsequently passed separately. The cause of that earlier SIGTERM is not established.

Additional PostgreSQL coverage reproduced `test_postgresql_live_ingest_recovers_when_current_identity_reconciliation_wins_owner_lock` failing both on this candidate and on an archived copy of fork HEAD `2930dec0` with the same interpreter/dependencies. Its barrier signalled before the first SELECT completed, allowing reconciliation's DELETE to commit before the intended pre-commit row was observed. The test now releases reconciliation only after that SELECT completes, retaining row identity, both usage windows, values, task cleanup and timeout assertions. No usage-ingestion runtime code was changed for this issue; all 40 account-deletion/live-usage integration tests then passed on PostgreSQL in 90.95 seconds.

Strict canonical OpenSpec validation reports 22 existing `Purpose` placeholders. Each placeholder was compared against fork HEAD and is unchanged. The normal validation passes; strict global validation is **not** green and has not been weakened or waived. The touched `upstream-proxy-routing` capability is among those pre-existing placeholders. Do not claim strict global readiness or archive this integration while the required gate remains unresolved.

Affected placeholder capabilities: `account-auth-export`, `account-identity`, `account-pool-usage-v1-usage`, `account-quota-presentation`, `account-routing`, `api-firewall`, `api-response-metadata`, `audio-transcriptions-compat`, `automations`, `bridge-ring-membership`, `clipboard-copy-fallback`, `files-upload-protocol`, `fleet-summary`, `live-usage-ingestion`, `model-catalog-compat`, `proxy-warmup`, `rate-limit-reset-credits`, `release-automation`, `release-management`, `scheduler-coordination`, `unified-auth-export`, and `upstream-proxy-routing`. Resolve their existing `openspec/specs/<capability>/spec.md` Purpose sections in a separately scoped documentation change before rerunning the strict global gate; do not disable placeholder validation.

Local warnings and the unverified optional Helm cases remain distinguished from assertion failures. Cloud merge gates and current-head CodeRabbit threads for this uncommitted fork candidate have not been checked; upstream tag CI is not a substitute.

## OpenSpec verification scorecard

| Dimension | Assessment |
| --- | --- |
| Completeness | 14/15 tasks; local integration and test work complete; task 4.2 archive remains open |
| Correctness | 2/2 integration requirements and 6/6 delta scenarios mapped to implementation, lockfile and passing tests |
| Coherence | Design followed: non-committing pinned merge, retained quarantine/key auth, shared diagnostic helper, frozen dependencies, no deployment |

Critical before archive: task 4.2 remains blocked by the strict global gate's 22 unchanged Purpose placeholders. Complete the separately scoped documentation cleanup and rerun strict validation before archiving; do not mark this task done solely because runtime tests pass.

Warnings: investigate the aiosqlite late-thread teardown warning with resource tracing; retain the 100 skipped unit checks and one expected failure as explicit limitations; resolve the mixed-version writer issue before any production rollout. These are not waived by the passing test counts. No missing implementation or uncovered scenario was found in this integration's two delta requirements.

Final assessment: local beta.4 integration passes the executed functional/build gates, but this change is **not ready for archive or production approval**. No archive command was run.

## Production handoff prerequisites

Production still runs beta.1 and was not updated. Before any separately authorized rollout:

1. Resolve the mixed-version bridge writer risk described in [context.md](context.md). Old replicas do not enforce immutable `abandoned` operations. A shared database being structurally compatible does not make a mixed-version rollout or arbitrary rollback safe; review a compatibility-first strategy before deployment.
2. Recheck HA serving/draining state, readiness, memory/database headroom, installed image contents and fixed AnyIO/native helper versions.
3. Use the `codex-lb-ha-deploy` skill and only `./scripts/deploy-compose-ha.sh deploy` for an authorized existing-HA rollout. No direct Compose recreation or manual runtime-state edits.
4. Verify three healthy serving backends, no remaining surge eligibility, public readiness, and request/settlement/cancellation health after rollout. Rollback requires an explicit operator request and the script's visible healthy-drain precondition.

Current read-only aggregate evidence showed 101 HTTP proxy endpoints without credentials. Beta.4 accepts credential-bearing HTTP/SOCKS endpoints with warnings, but their proxy-hop credentials would remain unencrypted; this update does not add or alter any endpoint.

## Subsequent operator deployment authorization

After the local verification handoff, the operator explicitly requested "commit, push, deploy" on 2026-09-07. This authorizes committing the prepared integration, pushing the existing fork branch and one existing-HA surge rollout; it does not authorize PR merge, archive-gate bypass, rollback, first-time bootstrap or manual runtime/database repair. The preceding authority and readiness sections describe the local verification checkpoint, not the later operator action.

Preflight: topology `blue,green,amber`, phase `none`, three UP backends at weight 1, surge ineligible at weight 0. Available host memory was approximately 11 GiB and PostgreSQL reported 51/100 connections. A read-only aggregate query found four completed and one failed bridge operation, with no unknown, acknowledged or abandoned operation. No request bodies, account credentials or proxy secrets were inspected.

Deployment decision: proceed with the authorized script-owned sequential rollout under monitoring. The new abandonment sweep requires at least 30 minutes without operation progress plus an expired-owner grace period, while each normal base-slot drain is bounded to 300 seconds. No currently persisted ambiguous operation can trigger the previously documented mixed-version writer hazard at this checkpoint. Recheck operation states while building and replacing replicas; this observation is not a general mixed-version compatibility guarantee or permission to resume an arbitrarily prolonged partial rollout. Any interrupted rollout requires fresh diagnosis of surviving old writers before a further operator action.

Only `./scripts/deploy-compose-ha.sh deploy` may change the topology. The unrelated untracked `simplify-install-one-liners/` remains outside the commit and is not copied into the runtime image. Strict global OpenSpec validation still has 22 baseline Purpose warnings; the change remains unarchived. Deployment outcome will be reported from actual post-rollout status, runtime version/dependency checks and public readiness, not inferred from a successful image build.
