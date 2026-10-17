# Verification — 2026-09-08

Base: local `origin/main` at `ae330ba9e`, isolated detached worktree
`/home/ubuntu/work/codex-lb/rust-sse-framing`.

## Results

- `CARGO_TARGET_DIR=/home/ubuntu/projects/codex-lb/target make rust-check`:
  formatting, Clippy with denied warnings, 19 Rust tests, locked release build passed.
- `make rust-audit`: advisories, bans, licenses, and sources passed; existing
  duplicate-version warnings remain. Neither dependency lockfile changed.
- Python focused combined run: **201 passed**, including real Rust worker
  integration (8 new SSE cases plus the existing routed HTTP/WebSocket probe),
  native adapter/packaging, 11 shared framing fixtures, SSE utilities,
  TTFT regressions, Codex client, and CI selection/workflow tests.
- Existing proxy SSE/idle regression selection: **56 passed** (the earlier
  64-test run also included the initial 8 shared fixtures).
- Ruff check/format and Ty passed for changed Python code and tests.
- Proxy architecture fitness gates passed.
- Strict OpenSpec validation: 58 main specifications passed; change delta passed.

The real-worker tests prove Python byte framing is bypassed for the migrated
path, byte limits precede UTF-8 replacement expansion, activity on partial events
resets the idle deadline, status/error bodies and non-streaming JSON remain
readable, and cancellation in both ordinary and cancelled AnyIO scopes leaves
an unrelated live request usable. A new cancellation regression test failed
before the cleanup fix and passed afterward.

## Scope and next slice

This is a source-level migration of direct native HTTP Responses SSE framing.
No production deployment, public upstream traffic, commit, or PR was performed.
No throughput or CPU improvement is claimed; performance needs separate
measurement. Future work can transfer routed HTTP framing after its response
abstraction explicitly carries framed-event ownership.

## Reproduce focused Python verification

Set `CODEX_LB_NATIVE_EGRESS_TEST_BINARY` to the built worker and run:

```sh
python -m pytest -q \
  tests/unit/test_native_egress.py \
  tests/unit/test_native_egress_packaging.py \
  tests/unit/test_native_sse_fixtures.py \
  tests/unit/test_sse.py \
  tests/unit/test_ttft_optimization.py \
  tests/unit/test_codex_client.py \
  tests/integration/test_native_sse_egress.py \
  tests/integration/test_native_routed_egress.py \
  tests/unit/test_ci_workflow_required_checks.py \
  tests/unit/test_github_ci_scripts.py
```
