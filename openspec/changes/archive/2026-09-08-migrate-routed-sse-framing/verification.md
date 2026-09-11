# Verification — 2026-09-08

Worktree: `/home/ubuntu/work/codex-lb/rust-sse-framing`, detached base
`ae330ba9e` plus the preceding uncommitted direct SSE migration.

## Result

Account-routed Responses HTTP streams now select the same Rust SSE framing
contract as direct streams. `CodexClient` passes typed options to unbuffered
native attempts and retains endpoint fallback and route metadata ownership.
The Responses adapter keeps the native framed-event interface visible.

No worker protocol, dependency, lockfile, operator setting, or public response
schema changed in this slice. Native HTTP errors and non-streaming bodies stay
raw. Missing-helper requests retain Python framing on the resolved proxy.
Compact framing remains outside this slice. No deployment, commit, push, or PR
was performed, and no performance improvement is claimed without measurement.

## Evidence

- Combined focused Python verification: **300 passed** in 74.58 seconds.
  This includes native adapter, packaging, shared fixtures, SSE utilities, TTFT,
  Codex client, direct/routed native wire tests, CI selectors, and the entire
  `tests/integration/test_proxy_responses.py` suite.
- Additional proxy regression selection: **58 passed**, 1314 deselected
  (`tests/unit/test_proxy_utils.py -k 'sse or routed or stream_responses_raw_route'`).
- `CARGO_TARGET_DIR=/home/ubuntu/projects/codex-lb/target make rust-check`:
  formatting, Clippy with denied warnings, **19 tests**, and locked release
  worker build passed. Dependency lockfiles remain unchanged.
- Ruff check/format, Ty, proxy architecture fitness, and `git diff --check`
  passed for the final code. Strict OpenSpec delta and all **58** main specs passed.

The real-worker suite now has **22** cases: shared direct/routed framing,
partial-body activity, idle and size failures, raw error/non-streaming bodies,
ordinary and cancelled-scope stream close, endpoint fallback/trace/no-replay,
missing-helper Python fallback, and owned-client cleanup.

A refused first endpoint is followed by a functioning local proxy with a third
endpoint that must never receive a replay. The tests assert the exact selected
route and fallback flag after success, size failure, idle timeout, and body
truncation. Hostile environment proxy values and `NO_PROXY=*` do not override
the explicitly resolved proxy. The owned-client case proves that the injected
native worker is used when `CodexClient` is constructed inside Responses.

Two regressions were demonstrated before their fixes:

1. Routed large-event consumption invoked the forbidden Python byte scanner.
   Preserving the native response interface and supplying options fixed it.
2. Closing a locally owned routed client in an already cancelled AnyIO scope
   interrupted its asynchronous session close. The existing deferred-cancellation
   cleanup helper now completes close before propagating cancellation.

## Reproduce

Build the workspace worker, set `CODEX_LB_NATIVE_EGRESS_TEST_BINARY` to the
resulting executable, and run:

```sh
python -m pytest -q --timeout=30 \
  tests/unit/test_native_egress.py \
  tests/unit/test_native_egress_packaging.py \
  tests/unit/test_native_sse_fixtures.py \
  tests/unit/test_sse.py \
  tests/unit/test_ttft_optimization.py \
  tests/unit/test_codex_client.py \
  tests/integration/test_native_sse_egress.py \
  tests/integration/test_native_routed_egress.py \
  tests/unit/test_ci_workflow_required_checks.py \
  tests/unit/test_github_ci_scripts.py \
  tests/integration/test_proxy_responses.py
```
