# Verification: block-paused-websocket-reuse

## Completeness

The new direct WebSocket availability requirement is implemented in
`app/modules/proxy/_service/websocket/mixin.py`. Main requirements and context
are synced. The HA command finished surge retirement successfully; all three
base backends are healthy on the verified image, and public readiness passes.
Detailed post-rollout observations are recorded in context.md.

## Correctness

| Scenario | Evidence |
| --- | --- |
| Idle unavailable account | Eight integration cases across both routes and four unavailable statuses verify a committed snapshot update retires A and sends only on B. |
| Pinned response owner | A real dashboard pause followed by previous_response_id fails closed without dispatch to either A or B; normal owner selection remains unchanged. Existing file-owner and model-source guard tests pass. |
| Accepted sibling | A created response completes on A after the unsent sibling is rejected; the next movable turn completes on B. |
| Pause during admission | A committed pause during create-lease acquisition blocks send and releases the reservation, work admission, create lease, and gate. |
| Available fast path | Existing same-owner follow-up tests still skip selector revalidation. |
| Cancellation during rejection | Both logging and terminal-delivery cancellation retain request_state_to_fail until scope finalization releases the unsent request's admission and create lease. |

208 distinct relevant tests passed across the dedicated availability, existing
WebSocket, cache invalidation, and model-source guard suites. After the cleanup
ownership addition, 31 affected tests were rerun and passed. Focused lint,
format, type, architecture, cancellation, timing seam, and settings tier checks
passed. Detailed commands and production observations are in context.md.

## Coherence

The implementation reuses the existing availability snapshot and cleanup/owner
selection paths. It introduces no status queries on ordinary available socket
reuse, no new setting, no schema change, and no account health penalty for local
pause rejection. Cross-account continuations retain existing ownership rules.
Snapshots remain eventually consistent through the existing invalidation bus.

## Validation limitations

Strict validation of this change passes. Full main-spec validation reports the
same 16 failing specs as unmodified HEAD (49 of 65 pass), with no added warnings
or errors. Test runs reported existing Starlette/SQLite teardown warnings.
Production request starts are inferred from completion time minus latency;
counts only include requests already recorded. Upstream overload and exhausted
owner failures can remain independently of the corrected paused-socket reuse.

## Pre-push checks

Repository-wide Ruff lint and formatting passed. Full `ty check` identified
optional TestClient portal accesses in the new tests; explicit lifespan portal
assertions resolved all diagnostics and the full type check then passed. These
test-only assertions do not change the deployed application. All 13 dedicated
availability integration cases passed again after these assertions were added.

The required `uv run pre-commit run local-ci --hook-stage manual --all-files`
was attempted but could not start because `make` is absent from this environment.
The full CI parity gate is therefore unverified; focused test results and
successful lint/type checks are not a substitute for that gate.
