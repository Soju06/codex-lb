# Astra conversation protocol verification

## Completeness and coherence

All five scoped requirements are implemented, covered by local regressions and synchronized to the main specs. Async continuity, steering ownership and inherited configuration policy form one Responses lifecycle change. Catalog and pricing additions are separate.

## Local checks

- Final verification after review fixes: 228 tests passed in 67.61s across all ten test_astra_* files (96 tests) and the existing test_proxy_websocket_responses integration suite.
- 326 existing tests passed in test_request_policy, test_proxy_api_responses_contract, test_openai_compat_features and test_model_source_routing.
- Ruff check and format check passed for all 24 changed/new Python files.
- Ty passed for all 14 changed/new app files.
- check_proxy_architecture.py, check_cancellation_safety.py and git diff --check passed.
- `openspec validate archive/2026-09-04-support-astra-codex-conversations --type change --strict --json --no-interactive` passed.
- `openspec validate --specs` completed with 48 passing and 10 failing specs. A strict JSON run has the same 28 error tuples as baseline dd28d7df; no new errors were introduced.

The test invocations used `-B -m pytest -p no:cacheprovider`, the respective files above and `-q`. These results were obtained on the independent protocol branch without the catalog/pricing patch. The final selection includes all newly added regression cases; the separate 326-test compatibility selection is unchanged by the final steering fixes.

## Committed-candidate review

The independent candidate review found three steering defects: stale successor ownership, retained byte budget after a failed submission, and empty structured text. Each was reproduced before its fix and covered by a regression. The final WebSocket dispatch regression also proves that late created/completed events leave unrelated request identity, admission and request logs unchanged; suppressed successor state is discarded at its terminal frame.

A proposed late-anchor reservation finding was compared against the baseline using identical frames. The opaque-context reservation gap exists on the baseline and is not repaired by this scoped change. Its generic admission behavior is tracked separately in [#2090](https://github.com/Soju06/codex-lb/issues/2090); completed-response settlement is unchanged.

## Requirement coverage

- Async function/custom calls survive intervening turns; typed actual outputs consume matching pending calls, while synchronous calls retain interruption repair. Durable manifests unable to represent async state are not authoritative.
- Steering tests cover automatic/explicit successors, multiple accepted steers, early required outputs, unrelated queued creates, errors before acceptance, anonymous error correlation, wrong lanes and limits.
- Admission, account lease, heartbeat and usage ownership are exercised through partial failures and cancellation. Disconnects do not replay accepted steering.
- Request policies cover allowed/enforced updates, inherited response/conversation anchors, proxy-injected anchors, repeated Ultra preparation and final wire normalization.
- Source routing tests preserve source-specific contracts and require subscription validation after selection. Explicit compaction stays on Responses; automatic and standalone compaction with updates are rejected.

## Scope and limits

Initial verification on 2026-09-04 and final review-fix verification on 2026-09-05 (Europe/Stockholm; 2026-09-04 UTC) against baseline dd28d7dff94cdd4919067c1986fd9606b9bbc6b9 using deterministic local fixtures and an existing Python virtual environment. No provider call or production change is part of this verification. Account availability and subscription acceptance remain unverified.

Strict global spec validation reports 48 passing specs and 10 failing specs, with 28 errors. A temporary extraction of the baseline produces exactly the same error tuples (spec, level, path, message). The scoped archived change passes strict validation. Existing global failures are not repaired by this feature.

## Publication checks

The initial full `UV_FROZEN=1 uv run pre-commit run local-ci --hook-stage manual --all-files` invocation stopped on typing errors in new tests. Those errors were corrected in 397b9e3f, and the remaining component targets were exercised separately on that head:

- Frontend lint, type check, build and all 1,227 tests passed.
- `UV_FROZEN=1 make lint typecheck test-unit`: 7,741 passed and 97 skipped; repository-wide static checks passed. The two Helm unit files were then run with the required tools: 107 passed with no skips. Three unit skips are existing obsolete concurrency cases.
- `UV_FROZEN=1 make test-postgres migration-check migration-check-postgres`: 167 passed; SQLite/PostgreSQL migration checks passed with no schema drift.
- `UV_FROZEN=1 make test-integration-core`: 2,197 passed, 35 skipped and one timing-sensitive failure in `test_prestop_commits_deadline_before_sigterm_and_cannot_reopen`. That test passed on its isolated rerun (1 passed), and its complete unchanged shutdown file then passed (5 passed). The fixture completes 250 ms after draining starts, so process startup under load can miss its in-flight observation. The test, fixture and shutdown implementation are unchanged by this PR. The original broad run is not recorded as green.
- `UV_FROZEN=1 make -o frontend-build test-integration-bridge test-e2e`: 286 bridge tests passed, followed by 27 E2E tests passed and one opt-in installed-Codex test skipped.
- Rust formatting, Clippy, tests, release build and dependency audit passed. Package build and wheel asset verification passed. Docker build and the configured critical-vulnerability check passed.
- Helm lint, templates and schema validation passed for Kubernetes 1.32 and 1.35. Local kind smoke checks passed for both bundled PostgreSQL and external PostgreSQL with two replicas.

Review repairs receive fresh affected-path validation; these component results are evidence for the published 397b9e3f candidate, not a claim that a later head ran the whole gate. GitHub CI and current-head review remain separate merge gates. Local test completion does not establish live subscription acceptance or deployment readiness.

## Cloud-review regressions

- Completed-parent retention: the new dispatch regression failed in both non-Astra cases before the fix and all four parameterized cases passed after it. Successful Astra responses retain their owned steering parent; successful non-Astra responses clear it.
- `.venv/bin/python -B -m pytest -p no:cacheprovider tests/integration/test_astra_request_policy.py tests/unit/test_astra_inherited_policy.py tests/unit/test_astra_completed_retention.py -q`: 35 passed after the retention repair and specification clarifications. This covers explicit compaction on both Responses routes and preservation of client-plane Ultra through repeated policy preparation.
- The independent review of the frozen quota repair found no blocking issue in reservation extension, rollback, per-submission reduction, terminal settlement or cancellation cleanup.
- Late HTTP-bridge anchor regressions cover both restored-anchor lookups: original 400/403 validation errors preserve a reusable session, while a real persistence I/O failure still retires it with 502. Ultra egress is checked for canonical, uppercase and whitespace-padded Astra model IDs.
- Direct WebSocket regressions reject high configuration updates under both allow-only-low and enforced-low API keys before reservation or account selection, and steering failures suppress raw exception text. Configured sources retain their own model controls as required by the source-selection contract.
- `.venv/bin/python -B -m pytest -p no:cacheprovider tests/integration/test_astra*.py tests/integration/test_proxy_websocket_responses.py tests/integration/test_api_key_reservation_extension.py -q`: 171 passed after all runtime review repairs.
- `UV_FROZEN=1 make lint typecheck`: repository-wide Ruff, formatting, architecture/cancellation and Ty checks passed on the combined repair. Reservation-extension tests are included in the standard PostgreSQL Makefile target as well as the integration suite.
- With `CODEX_LB_TEST_DATABASE_URL` selecting the isolated local PostgreSQL database, `.venv/bin/python -B -m pytest -p no:cacheprovider tests/integration/test_api_key_reservation_extension.py tests/integration/test_api_keys_api.py -q`: 104 passed, including rollback, concurrent finalization/release and the existing API-key product routes.
- `UV_FROZEN=1 make test-unit` with the required Helm tools available: 7,846 passed, 3 existing skips and 2 failures caused by a test double still exposing the former unlocked reservation-read method. Updating that one double to `get_usage_reservation_for_update` preserves the original failure/settlement assertions; its two parameterized cases then passed (`.venv/bin/python -B -m pytest -p no:cacheprovider tests/unit/test_proxy_utils.py -k stream_with_retry_releases_api_key_reservation_when_owner_lookup_fails -q`), followed by file-scoped Ruff/format/Ty checks. No runtime code changed after the broad run.

## Follow-up review: reservation edge cases

- Reservation extension now depends only on an existing continuation, independently of parent-body migration. A new successor therefore reserves its input once even when the parent already retains steering configuration.
- A failed rejected-input refund is logged and returns false; the rolled-back reservation remains available for terminal reconciliation and the WebSocket continues processing other responses. Extension failures still reject admission.
- The rollover regression changes one quota window after admission and proves that a partial reduction rolls back both limit and ledger changes. Finalization succeeds without changing the new window's usage or reset boundary.
- The bridge fixture already installed test-local Settings. It now uses `monkeypatch.setattr` for the recovery-mode field as well, making restoration explicit; all nine bridge regressions pass.
- Focused checks: 28 Astra protocol, 116 API-key service/proxy unit and four reservation integration cases passed. Repository-wide `UV_FROZEN=1 make lint typecheck` passed. Strict scoped OpenSpec passed, and strict main-spec validation still matches the same 28 baseline error tuples.
- The combined `.venv/bin/python -B -m pytest -p no:cacheprovider tests/unit/test_astra*.py tests/integration/test_astra*.py tests/integration/test_proxy_websocket_responses.py tests/integration/test_api_key_reservation_extension.py -q` selection passed all 254 cases after these changes.
- Independent review of refund containment, cancellation, first/additional-steer admission and the rollover regression found no blocking issue.
