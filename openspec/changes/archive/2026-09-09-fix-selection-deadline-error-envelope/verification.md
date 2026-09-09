# Selection-scope error-envelope verification

## Candidate and scope

This follow-up is applied on the uncommitted beta.5 plus #2078 candidate. Fork
HEAD remains `7982375e8a86a5d0c5caf13de0c91458b43c4165`, and MERGE_HEAD peels to
`b54696466d8e073832b37dd0655a14ca776fc00b`. No commit, push, PR, deployment,
production database access or HA state mutation is part of this change.

The runtime patch adds one typed scope-mismatch fact to `StickySelectionOutcome`
and `AccountSelection`, derives it from the authenticated pre-filter policy
pool, and makes the shared recovery helper decline an impossible wait. It does
not cache prior errors, catch arbitrary timeouts, reduce request budgets or
relax owner selection. Existing stronger required-owner envelopes remain intact.

## Baseline evidence

Before the fix, the candidate's full integration core run reported 2,598 passed,
34 skipped and one failure: the API-key-scoped hard owner produced
`upstream_request_timeout` instead of `hard_affinity_saturated`. Complete
sticky-session module runs with two workers reproduced the same failure on both
beta.4 and the candidate (41 passed, one failed each). Isolated runs of that
single test passed on both trees after about 77 seconds, which did not supersede
the failing module results. Both trees used the same candidate dependencies;
this is a source comparison, not a claim about production's dependency versions.

The owner is resolved correctly but is outside the key's account scope. Repeated
capacity recovery cannot make that owner authorized and can instead expire the
75-second selection budget. The fix terminates on the resolved policy mismatch,
while genuine unresolved selection and upstream-work deadlines retain their
existing classifications.

## Requirements, scenarios and design

| Dimension | Evidence |
| --- | --- |
| Completeness | 9/9 tasks complete; one added requirement and four scenarios covered; verified and archived |
| Correctness | Scope is captured before health/model/exclusion/cap filters; no cross-account dispatch or owner mutation |
| Coherence | Existing typed selection contracts, shared recovery helper and injected virtual timing seams; no per-transport workaround |

| Scenario | Implementation and regression evidence |
| --- | --- |
| API-key-scoped hard owner fails closed near deadline | `sticky_selection.py` final outcome and `load_balancer.py` failure result carry the fact; `support.py` recovery helper skips waits. Public route tests cover ACTIVE and QUOTA_EXCEEDED out-of-scope owners, one exact terminal, one selection, unchanged durable owner and zero pressure/leases on both accounts. Virtual unit tests use 0.01, 2 and 75 seconds of remaining budget. |
| Upstream work timeout remains distinct | Streaming timeout/connection-provenance controls in `test_proxy_utils.py`; the generic deadline translation is unchanged. |
| Eligible local capacity keeps recovery semantics | Shared wait-helper controls, WebSocket capacity-budget controls and virtual in-scope cases still wait/retry and drain all timers. |
| In-scope owner remains recoverable | Load-balancer exclusion and quota controls, direct-stream transient exclusion recovery and bridge transient file-owner recovery; API-key/security scope mismatches alone are marked terminal. |

Bridge regressions additionally verify handoff cleanup and no upstream socket
open for both ordinary hard-affinity and stronger required-owner envelopes.
The WebSocket regression verifies one terminal send, one selection and no
elapsed virtual time or pending timers. The public SSE route may emit its normal
initial transport heartbeat; it must not emit capacity-wait progress.

The requirement block exactly matches the canonical
`openspec/specs/responses-api-compat/spec.md`; stable rationale is synced to its
`context.md`. All nine beta.5/quota/deadline blocks were compared, without
overwriting unrelated canonical requirements.

## Check results

Reports are in `/tmp/codex-lb-beta5.h1f5mw`. Counts overlap and are not additive.

| Check | Result |
| --- | --- |
| Focused scope/recovery/load-balancer/bridge/WebSocket tests | 15 passed (`scope-recovery.xml`) |
| Final strengthened public Responses route cases | 2 passed (`scope-route-final.xml`) |
| Timeout and recoverable-capacity controls | 28 passed (`scope-controls.xml`) |
| Sticky-session module after runtime fix, before adding the second owner-status parameter | 42 passed (`sticky-sessions-fixed.xml`); final expanded module is included in the broad integration run |
| Final expanded sticky-session module, two workers | 43 passed in 19.33 seconds (`sticky-sessions-final.xml`) |
| Mutation: clear only the new mismatch flag in the shared helper | Six expected failures (three virtual out-of-scope cases, two bridge mappings and one WebSocket terminal); three in-scope controls still pass (`scope-mutant.xml`) |
| Initial full unit/simulation rerun | 8,493 passed, two failed, 100 skipped, one expected failure; the two failures were old `SimpleNamespace` doubles missing the new typed field |
| Corrected typed test doubles and final full unit/simulation rerun | 8,495 passed, 100 skipped, one expected failure, 15 warnings; 217.54 seconds (`unit-verified.xml`) |
| Final full integration core | 2,600 passed, 34 PostgreSQL-only skips, zero failures, 62 warnings; 1,024.51 seconds (`integration-final.xml`) |
| Final real native SSE/routed helper and graceful shutdown | 28 passed in 11.01 seconds (`native-shutdown-final.xml`) |
| Final PostgreSQL 18 supplement | Four passed in 12.35 seconds: both strengthened scope-route cases and two previously uncovered SQL-shape cases (`postgres-final-gap.xml`) |
| Ruff lint/format, ty, architecture/timing/cancellation checks | Pass; 1,062 Python files formatted |
| Canonical OpenSpec normal and strict | 59 passed, zero failed, using CI-pinned OpenSpec 1.11.0 |
| Change strict validation and whitespace check | Pass |
| Python wheel and source distribution | Rebuilt in `dist-final/`; packaged selector, sticky selector, recovery helper and native adapter exactly match candidate source |

The mutation plugin ran in its own pytest subprocess and changed no application
files. The two old loose mocks now instantiate `AccountSelection` and
`StickySelectionOutcome`; runtime code did not acquire a fallback for malformed
test doubles. Earlier new-test harness mistakes (lazy service lookup, an
overbroad heartbeat assertion and an optional mock-call type narrowing) were
corrected before the reported focused passes. No production assertion was
weakened, and the exact terminal code remains mandatory.

## Reproduction commands

Use Python from `.venv`, unset `CODEX_LB_TEST_DATABASE_URL`, set
`CODEX_LB_ENV_FILE=/tmp/codex-lb-beta5.h1f5mw/no-env`,
`CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN=beta5-disposable-test-token`, and
`TMPDIR=/dev/shm/codex-lb-beta5-tests.JEBC1Z`. These are disposable test settings.

```sh
.venv/bin/python -m pytest -q tests/unit/test_selection_scope_recovery.py tests/unit/test_load_balancer_concurrency.py tests/unit/test_proxy_http_bridge.py tests/unit/test_proxy_utils.py tests/integration/test_proxy_sticky_sessions.py -k 'scope_mismatch or distinguishes_scope or hard_codex_session_owner_outside_selection_pool or unusable_hard_codex_session or cannot_retire_owner_outside_api_key_scope' --timeout=60 --timeout-method=thread
.venv/bin/python -m pytest -q tests/integration/test_proxy_sticky_sessions.py -k cannot_retire_owner_outside_api_key_scope --timeout=60 --timeout-method=thread
.venv/bin/python -m pytest -q tests/unit/test_proxy_utils.py tests/unit/test_proxy_http_bridge.py -k 'account_selection_recovery_sleep or scope_mismatch or retries_transient_file_pin_owner_saturation or select_websocket_capacity_wait_budget or keeps_budget_timeout or preserves_connect_timeout or retries_hard_owner_after_transient_exclusion' --timeout=60 --timeout-method=thread
.venv/bin/python -m pytest -q -n 2 --dist worksteal --timeout=180 --timeout-method=thread --maxfail=5 --tb=short --junitxml=/tmp/codex-lb-beta5.h1f5mw/unit-verified.xml tests/unit tests/simulation tests/test_request_logs_options_api.py
.venv/bin/python -m pytest -q -n 2 --dist worksteal --timeout=180 --timeout-method=thread --maxfail=5 --tb=short --junitxml=/tmp/codex-lb-beta5.h1f5mw/integration-final.xml tests/integration --ignore=tests/integration/test_native_sse_egress.py --ignore=tests/integration/test_native_routed_egress.py --ignore=tests/integration/test_graceful_websocket_process_shutdown.py
npx --yes @fission-ai/openspec@1.11.0 validate --specs --strict
npx --yes @fission-ai/openspec@1.11.0 validate fix-selection-deadline-error-envelope --strict
uv build --out-dir /tmp/codex-lb-beta5.h1f5mw/dist-final
```

## Limitations and archive gate

Final unit/integration results are green. Optional Helm and
environment/version-specific skipped cases, the pre-existing lifecycle xfail
and test-harness warnings remain visible; skips are not counted as passes.
The rebuilt real helper, process-shutdown supplement and both scope-route cases
on PostgreSQL also passed after this fix. All 34 PostgreSQL-only cases skipped
by SQLite map to passing PostgreSQL reports. Frontend and browser evidence from
the earlier integration is recorded in the beta.5 report; those sources did not
change here. The final disposable PostgreSQL container was stopped and removed,
discarding only its synthetic tmpfs database; report files remain available.

This fix applies once selection has resolved an out-of-scope hard owner. It does
not claim to turn a database/network timeout that prevents resolution into an
ownership error. In-scope account recovery still uses existing bounded waits.
The separate unbounded HTTP-event-queue risk and all deployment prerequisites
remain unchanged. No cloud merge gates have been claimed.

No critical implementation, scenario-coverage or design-coherence finding
remains. Local verification is complete. The documented skipped/xfail cases and
warnings are retained as limitations, not silently treated as passing coverage.

Archived on 2026-09-09 after all local gates passed. Delta requirements and stable
context were already synced; no additional spec mutation was needed at archive.
Only this change was moved, preserving its metadata and evidence. The related
beta.5 integration is archived separately as
`2026-09-09-sync-upstream-v1-25-beta-5`.
