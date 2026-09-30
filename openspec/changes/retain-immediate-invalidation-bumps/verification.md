# Verification — 2026-09-30

Base: unchanged upstream `main@f8ffbac20`; implementation branch `fix/retain-immediate-invalidation-bumps`. Python 3.13.10, dependencies installed with `uv sync --frozen --python 3.13`. No production services, credentials, or data were used.

## Reproduction

Before changing the shared implementation, the new unit selection produced **8 failed / 4 passed**. Failures proved lost direct notifications after exhausted/unexpected writes, consumption of older pending work, direct marker races, cancellation before/after commit and during backoff, and ambiguous driver failure. The real API regression produced **1 failed / 1 passed**: disabling a key committed and returned 200, but the peer's active auth cache survived a recovered source poll. The account-pause control already retained its prequeued marker on main.

Local reproduction logs: `/tmp/codex-lb-invalidation-baseline-unit.log` and `/tmp/codex-lb-invalidation-baseline-api.log`. These temporary logs are not repository artifacts and may contain disposable test bootstrap material.

## Passing checks

- Cache/auth/account/settings/upstream-route/multi-replica selection: **353 passed** across 14 files (`/tmp/codex-lb-invalidation-regressions.log`).
- Auth manager/dashboard auth/reset-credit store/API/scheduler/replica safety/automations selection: **262 passed** across 8 separate files (`/tmp/codex-lb-invalidation-security-regressions.log`). Together these disjoint focused selections total **615 passed**.
- The first focused core selection passed **38 tests** before adding four accepted-but-interrupted real-database commit cases; those four are included in the 353-test run.
- Coverage includes the real key-disable PATCH and peer `/v1/usage` denial with `401 / invalid_api_key`, real account pause with persisted status and peer snapshot convergence, independent sessions, direct/coalesced cancellation and marker races, retry-backoff cancellation, real accepted commits followed by driver failure/cancellation and safe duplicate version increments, namespace non-starvation, failed callback retry, and successful `bump_local()` source suppression.
- `make lint`, `uv run --frozen ty check`, simplicity budgets, strict change validation, all **67 stable specifications**, and whitespace checks pass. Migration topology is unchanged: **263 revisions**, one head, no new revisions.
- The `make test-unit` prerequisite frontend build and frontend type compilation passed.

## Full unit gate limitation and negative control

`make test-unit PYTEST_ARGS='-q -x'` stopped at **1 failed / 1043 passed** in `tests/unit/test_codex_body_capture_guards.py::test_output_under_a_temporary_filesystem_is_refused`: the expected `CaptureRefusal` was not raised for `/tmp/codex-body-capture` on this macOS environment. This is **not** a passing full unit/simulation gate.

The exact unchanged test also fails from the clean original main checkout at `f8ffbac20`, using the same Python 3.13 environment (`/tmp/codex-lb-invalidation-main-baseline.log`). The test and body-capture implementation are unchanged by this PR. No test was weakened or unrelated baseline fix included.

## Independent review and coherence

Independent read-only Pi review using `openai-codex/gpt-5.5` reported **no actionable findings** (`/tmp/codex-lb-invalidation-independent-review.jsonl`). It reviewed the implementation, changed tests, and OpenSpec coherence; it did not rerun tests or inspect cloud evidence. Final self-review maps all 13 resilience scenarios to the new or existing passing cache-invalidation suites. The delta and stable resilience requirement match; only this requirement's behavior changes.

Production diff is confined to `app/core/cache/invalidation.py`. Namespace registrations, settings/upstream-route publication and caller fallbacks, API fields, migrations, UI, and #2463's usage-share implementation remain unchanged. Retention is in-process, not a durable outbox or shutdown guarantee; duplicate invalidations after ambiguous commit outcomes remain safe.

## Publication gates

No PR merge or production rollout is authorized. Local verification does not establish cloud readiness. Required GitHub CI (including PostgreSQL) and current-head CodeRabbit/human review remain to be checked after publication. Keep the change active until those gates permit final verification and archive.
