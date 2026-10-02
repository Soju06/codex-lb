# Verification and rollout handoff

## Scope and incident conclusion

At the initial read-only handoff, the warm HTTP bridge admission defect was reproduced and fixed locally; production remained unchanged. On 2026-10-02 the operator explicitly authorized publishing the patch and proceeding with the isolated rehearsal and documented rollout plan. Subsequent rollout evidence is recorded separately from the initial results below. See [design.md](design.md) for the historical timeline and limits of the evidence; this is not a finding of leaked historical leases or a claim that every upstream interruption shares this cause.

The deployed and original-main `request_submit.py` hash is `765848bc1fa8bc6edb330bc0bbff4dc7b57b6cf099be463df4fbd06b941339cd`. The fixed file hash is `2ab0bb6ee1c5a7b494609a0f311993aa2f510aa308d67535f73688868f8107fd`. Only this application file changes. There are no schema, dependency, quota-policy, configuration, or UI changes.

Local source baselines:

- Main baseline: `f8ffbac2099a113fba54dfd8d77774f5bca80ffa`, `../codex-lb-bridge-cap-fix`.
- Deployed baseline: `e77106e519d02df644089f865e1a2c9a02f6241d`, `../codex-lb-bridge-cap-repro`.

## Completeness, correctness, coherence

All six initial implementation/verification tasks are complete. The separately authorized publication and rollout tasks remain tracked in tasks.md. The modified stable requirement and delta are synchronized. The implementation follows the design: one immutable pre-lock snapshot, explicit effective caps, no locked settings fallback, and no change to existing lease ownership/finalization.

| Requirement scenario | Evidence |
| --- | --- |
| Dashboard override / warm ninth turn | `tests/integration/test_http_bridge_reacquire_caps.py`: both `/v1/responses` and `/backend-api/codex/responses`, keyed/unkeyed, cap 128 and unlimited, eight competing actual leases |
| Lower cap refusal and inherited cap | Same public-route matrix, dashboard cap 2 and null; checks error envelope, no upstream frame, no leftover lease/waiter/queue count |
| Updated and partitioned cap semantics | `tests/unit/test_http_bridge_reacquire_caps.py`: real balancer, cap changes across reacquisitions, all three shares of cap 5, null and unlimited |
| Settings I/O remains outside pending lock | `test_submit_resolves_snapshot_before_pending_lock`, keyed/unkeyed × cancellation/read error; confirms another task can take the lock and no lease was acquired |
| Held-lease fast path | `test_submit_with_held_lease_never_reads_settings`, keyed and unkeyed |
| Fair-share/lease ownership unchanged | Existing idle-lease suite: per-key denial/accounting, estimated-token budget, racing close, repeated cancellation, stale finalizer, prewarm/admission failure |

The public tests stub initial selection and upstream I/O, but use real warm bridge submission, settings resolution, balancer leases, request routing, and cleanup. Their initial-selection stub explicitly acquires the dashboard-capped lease. Retry sleeping is disabled to expose an erroneous 429 promptly rather than waiting for production-scale retries. They are not load tests or real-upstream tests.

## Executed checks

### Regression proof

Before changing application code, the initial eight public-route regressions **all failed** with `429 account_stream_cap`, on both untouched main and the exact deployed source. After the patch, all eight passed. The expanded matrix of **16 public-route cases** also passes on both baselines, including genuine lower/inherited-cap refusal.

### Main baseline plus patch (Python 3.14)

- Idle-lease suite + new unit/public-route matrices: **62 passed**.
- Full `test_proxy_http_bridge.py` + `test_load_balancer_concurrency.py`: **1,217 passed**.
- Full `test_http_responses_bridge.py` + `test_proxy_utils.py`: **1,610 passed, 1 failed**. The failure is the existing reader-handoff timeout described below; this broader run is **not green**.
- `make lint`, `ty check`, simplicity budgets, strict change validation, strict validation of all **67 stable specs**, and `git diff --check`: passed. Migration topology is unchanged and its checker passed.

### Deployed baseline plus identical application patch (Python 3.13)

- Idle-lease, new unit/public-route matrices, full proxy bridge unit and balancer concurrency suites: **1,281 passed**.
- Full HTTP bridge integration + proxy-utils suite: **1,609 passed, 1 failed**, the same reader-handoff timeout.
- The patch applies cleanly without pulling in current-main application changes. Existing candidate tests were adapted by the narrow test diff, not replaced wholesale by current-main tests.

### Known failure and harness corrections

`tests/integration/test_http_responses_bridge.py::test_v1_responses_http_bridge_idle_recovery_hands_reader_to_replacement` times out at 30 seconds with the patch. It also times out at **45 seconds on untouched main**, so it is not a new regression from this change. At the initial handoff its cause was unresolved. During the authorized image rehearsal, the unchanged test passed on both the original and patched deployed images in approximately 63 seconds with `--timeout=120`. The reader's `_HTTP_BRIDGE_EVENTLESS_RESPONSE_CREATED_MAX_SECONDS` watchdog is 60 seconds; patching keepalive count does not shorten that deadline. The earlier 30/45-second test-runner limits interrupted the expected watchdog, not a proven reader-handoff defect. The full main bridge/proxy-utils selection then passed **1,611 tests** at the adequate 120-second timeout. No application behavior, test assertion, fixture deadline, or test selection was changed to obtain those passes. See [deployment.md](deployment.md) for image-level evidence and cloud gates.

Early unit runs hung because newly settings-aware anonymous reacquisition reached an uninitialized unit-test database. The lifecycle unit fixture now supplies a settings-cache stub, while explicit blocked/error/cancel tests override it and route tests exercise the real path. An initial candidate test-copy attempt imported three current-main heartbeat tests whose helper does not exist on the deployed revision (**3 failed / 1,276 passed**). That test file was restored to its candidate original and only the required snapshot-return adaptation applied; the corrected selection passed all 1,281 tests. These were test-harness corrections, not application fallbacks.

## Independent review

Read-only review session `5f0d10fd-5c73-4c31-a2c8-ffab634c80c0`, `openai-codex/gpt-5.5`, found **no actionable findings**. The review specifically covered preregistration, prewarm, closed/reconnected sessions, the `snapshot=None` fast path, lower/unlimited/partitioned caps, cleanup, and route-test validity. The reviewer did not run tests or modify files.

Private local evidence is retained under `/tmp/codex-lb-cap-*`, including `baseline-main.log`, `baseline-deployed.log`, `focused.log`, `unit-broad.log`, `route-broad.log`, `main-baseline-idle.log`, `deployed-fixed.log`, `deployed-route-broad.log`, `validation.log`, and `independent-review.jsonl`. Historical production evidence is in `/tmp/codex-lb-labs-historical-stall.log` and `/tmp/codex-lb-labs-stall-selection-evidence.log`; do not publish raw logs or private configuration.

## Authorized rollout plan

At the final read-only check (2026-10-02 19:19 UTC), production still runs `codex-lb:beta9-candidate-e77106e5`, image ID `sha256:496fd93c85ae90d18dbf4c085a005e84035e18a021886b6e6f944ab0a7b4ff67`, with zero restarts since September 18. The original source hash remains present. Local health/readiness return 200; this does not validate warm admission.

1. Obtain explicit authorization for publication and a production rollout. Do not deploy current main or bundle unrelated PRs as an incident workaround. Address or explicitly assess the separate baseline reader-handoff timeout before asserting the whole bridge is healthy.
2. Build an immutable minimal candidate from the exact deployed image/source plus this reviewed application patch. Record its image digest and verify the fixed-file hash above. The local source-only patch SHA-256 is `c4f8060a7425034ba01991bb3717cea44f67469049ce6c359ae703b9dd175aeb`; no candidate image has yet been built.
3. Rehearse that image with an isolated disposable database, stub upstream, and no production volume, port, or ring identity. Repeat the warm-turn test with eight occupied leases and cap 128, lower/unlimited/inherited cases, cancellation cleanup, and account pressure returning to its starting value. Local source tests are already complete but are not an image rehearsal.
4. Preserve the current compose/image reference and take a consistent backup through the existing deployment runbook. Verify schema expectations are unchanged. Drain active requests using the supported procedure; a single-replica rollout can interrupt long-lived connections and needs an agreed window.
5. Switch only to the verified candidate. Validate readiness/ring membership **and** fresh/reused Responses traffic on both entry routes. Observe a representative load window for warm-only capacity retries, ownership 502s, terminal request completion, and healthy-account behavior. Do not raise caps or generate excessive live upstream load merely to test this patch.
6. Roll back on increased stalls, ownership errors, failed settlement, or readiness/ring problems: drain the candidate, restore the preserved previous image/compose reference, and restart using the normal deployment procedure. No schema downgrade is needed. Do not restore an old database over new request/accounting writes merely for an application rollback.

At the initial handoff, no production write, deployment, restart, limit adjustment, branch creation, commit, PR, merge, or archive had been performed for this change. The operator subsequently authorized publication, rehearsal, and rollout; subsequent evidence is tracked in [deployment.md](deployment.md). No upstream merge or archive is authorized by that operational approval; source validation is not cloud CI or maintainer approval.
