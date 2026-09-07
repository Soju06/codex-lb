# Pinned beta.4 integration

## Evidence and purpose

Assessment on 2026-09-07 compared fork `2930dec0` with upstream main `a8339c8f`; their common ancestor is `575c2087`. The initial candidate was beta.3 (`b9f598ce`, 49 upstream-only commits). The operator subsequently approved beta.4, pinned to `15ccd901bf013daa11c67934cc019a9b33f2f72b` (53 upstream-only commits). Upstream beta.4 CI Required passed. The prior beta.3 test results below remain historical evidence, not a substitute for beta.4 integration verification.

All three live backends reported application version `1.25.0-beta.1` and AnyIO `4.13.0`. Blue reported HTTP bridge enabled and transport `auto`; its native websocket byte budget is 1 GiB. The containers were healthy. Running the upstream deterministic release/cancel/acquire reproducer locally with AnyIO 4.13.0 returned `deadlocked` for Lock and Semaphore, while stdlib Lock acquired. This demonstrates vulnerability to the interleaving, not a diagnosis of an ongoing production deadlock.

## Scope and preservation map

| Area | Contract to retain | Validation surface |
| --- | --- | --- |
| Account quarantine | Every reauth-required account excluded regardless of JWT expiry; hard ownership fails closed | Eligibility, balancer and HTTP bridge tests |
| Key dashboard | Standalone Bearer authentication, profile privacy, remembered-key opt-in, installers | Frontend integration + key-dashboard API/install tests |
| Daily limits | Midnight Asia/Ho_Chi_Minh, persisted UTC, alignment preserves counters | Limit-window, scheduler and API tests |
| Account import and pools | Multi-file partial outcomes and automatic pool assignment | Import UI, account/OAuth API and assignment tests |
| Native egress | Existing queue policy, 1-GiB HA websocket limit and failure diagnostics | Native egress/buffer/websocket tests |
| Control routes | `/v1` search support, original content type, identity encoding | Daybreak/search/control route tests |
| HA | Three steady backends, temporary surge, shared PostgreSQL/key and unique identities | Compose deployment tests and later operator checks |

## Example and failure modes

If upstream accepts a key-dashboard browser navigation, its new administrator error boundary must not start `/api/dashboard-auth/session` before the self-service key screen. For routing, a `reauth_required` account with a JWT expiring tomorrow must still be excluded after the new caller-clock signature is adopted. Git can merge surrounding selection code without proving either behavior.

The native compression fix and the fork's `Accept-Encoding: identity` control fix operate at different layers. Keep the latter until wire-facing regressions prove a replacement is equivalent. A Python-only rebuild cannot deliver the Rust helper's new decoders.

`abandoned` is stored in an existing string column. The absence of a migration is not evidence that mixed-version writers or arbitrary rollback are safe. Record the old/new behavior before production rollout.

## Related contracts

See [proxy architecture delta](specs/proxy-architecture/spec.md), [key-dashboard delta](specs/api-key-dashboard/spec.md), and canonical `account-routing`, `api-keys`, `outbound-http-clients`, `responses-api-compat`, `deployment-installation`, and `replica-operations` specs. Stable integration context will be promoted only after verification.

## Integration observations

While integration was in progress, upstream published beta.4 (`15ccd901`) at 15:51 UTC. It includes the three originally excluded commits and a release bump. Its CI Required result subsequently passed, and the operator approved retargeting to beta.4. See [the beta.4 reassessment](beta-4-assessment.md) for exact scope, current production compatibility evidence, and additional validation.

The first backend import exposed a semantic merge conflict: the fork imported its failure-metadata helper from `websocket.helpers` into `http_bridge.upstream_events`, while the new websocket package imports the bridge's `request_submit`. This created a circular import. Moving the shared helper to `support` removes that cross-domain edge and preserves the phase/detail overrides. The architecture check then passed. Route-recovery, key-dashboard and multi-file import frontend integration passed (23 tests).

Targeted backend checks passed: 316 tests covering AnyIO, eligibility, limit windows/scheduler, Compose HA, native websocket capacity, pool assignment, search/control headers, installer output, and websocket transport; another 67 selected balancer, quarantine, metadata and cancellation tests passed. Python lint/type/format checks and architecture/timing/cancellation gates passed. Frontend lint/type/build passed. Canonical OpenSpec validation passed 59/59 in CI mode; strict mode reported 22 existing placeholder Purpose warnings, which are not changed by this integration.

Further beta.3 checks passed: all 1,245 frontend tests across 155 files; a separate rerun of 13 route-recovery tests including navigation from an unknown administrator path to the standalone key dashboard; all five real-browser smoke tests; native helper release build and its real-binary stream check; Rust format/clippy and all 12 workspace tests; SQLite migration upgrade/check at `20260830_000000_add_quota_warmup_claim_expiry` with no schema drift. Browser assertions passed, but backend shutdown emitted a ring-membership timeout/SQLite cancellation warning, which remains a verification caveat. The actual local Python interpreter reports 3.14.7, despite the older 3.13.3 environment note in AGENTS; test evidence is for the actual interpreter.

Long-running backend suites were interrupted to await the beta.3/beta.4 target decision, not treated as completed verification. The unit/simulation run stopped with 1,241 tests passed and no reported failures; its exit code 2 reflects KeyboardInterrupt. Integration progress likewise is not a passing full-suite result. Remaining task checkboxes stay open; no archive, commit or deployment has occurred.

## Mixed-version rollout constraints

`HttpBridgeOperationRecord.state` is a string column, so old readers can deserialize `abandoned`. The old submit path treats it as an existing ambiguous operation and fails closed with `503 upstream_operation_status_unknown`, rather than the new `400 previous_response_not_found` full-history recovery signal. This can delay recovery while old backends still receive continuations.

Old repository writers do not enforce the new immutable-abandoned predicate. A stale old owner whose lease expired but whose instance/epoch still match can append or update such a row; an old cross-session admission can also rebind it. The new sweeper protects valid database owner leases, applies an additional lease grace period, and protects locally pending operations, but that is not full mixed-version writer compatibility. Before production deployment, the operator must review whether a compatibility-first rollout is needed. Do not advertise arbitrary rollback to beta.1 as safe once abandoned rows exist; do not change database rows manually to work around this.

## Beta.4 implementation evidence

The original integration was saved under `/tmp/codex-lb-beta4-retarget.fy3R0Z/` before its agent-created merge was aborted. The new merge retains `HEAD=2930dec06bf9a76a5c29c6d15eb7c6311407dbfc` and `MERGE_HEAD=15ccd901bf013daa11c67934cc019a9b33f2f72b`, with no unresolved index entries. The unrelated `simplify-install-one-liners/.openspec.yaml` checksum remained `0d815ffdda0fec4cf9c7b2346a209fdec1bcde4f92cfb10c2075e2e381fd16f8`.

All prior integration fixes were retained. A broad test run exposed that the moved failure-metadata helper must preserve its original optional-attribute behavior for transport adapters/test doubles; direct attribute access had interrupted terminal cleanup. Restoring the original behavior and adding a missing-metadata regression fixed five failures. A file-affinity unit test now requests the database-reset fixture instead of depending on another test to create its schema. The six failing tests plus the metadata regression passed in isolation afterward.

Current beta.4 evidence includes 8,380 unit/simulation passes (100 skips, one expected failure), 1,248 frontend passes, 136 focused backend tests, 71 bridge/websocket retry/owner/settlement integration tests, 32 route/key/import/proxy-warning frontend tests, 10 key-dashboard API tests, all five browser smoke tests, three real-native-helper boundary tests, and PostgreSQL migration/concurrency checks. The actual PostgreSQL test server is 18 (matching the locally available image, not upstream CI's 16), isolated in a temporary container with no production volumes. Its migration check reports one current revision and no schema drift. See verification.md for final full-integration and PostgreSQL results.

An initial integration run with old workers and slow disk-backed SQLite reported a one-second commit timeout and a websocket EOF timeout. Both tests passed on the current tree with an isolated tmpfs database without changing their timeouts or assertions; the full suite was restarted with one fresh SQLite database per worker on tmpfs. A passing targeted retry run still emitted an aiosqlite late-thread/closed-event-loop warning, so test-harness cleanup remains a caveat until the final full-suite result. The beta.4 real-browser run completed cleanly without the earlier beta.3 shutdown warning.

Broader verification also aligned the imported virtual-clock test with this fork's status-based quarantine. Bootstrap tests require `CODEX_LB_ENV_FILE` to point outside the operator checkout: deleting an env var alone does not prevent `.env.local` discovery. The operator file and token were not edited. A PostgreSQL live-usage test failed on both fork HEAD and the candidate because its barrier released a deleting transaction before the expected MVCC read completed. Moving that test-only signal after the read preserves the intended overlap; all 40 account-deletion/live-usage PostgreSQL integration tests then passed. Runtime ingestion behavior was not changed.

Canonical specs now include the integration deltas and the imported beta.4 retry/proxy-policy deltas. Non-strict canonical validation passed all 59 capabilities; strict mode reports the same 22 Purpose placeholders already present in HEAD. The integration change and both imported beta.4 changes validate strictly. See [route screenshots](evidence/context.md) for before/after evidence.
