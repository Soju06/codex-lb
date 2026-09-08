# Verification — 2026-09-08

Base: `8c6467d979104f3294f823c80c526cf56fbbe29d` (main).
Python: CPython 3.13, existing frozen PR-2164 verification environment.
Native worker: built from this worktree with the committed Cargo.lock.

## Contract evidence

- Direct/routed real-worker compact tests reject Python byte scanning and
  return normalized output before HTTP EOF. Both failed before implementation
  (direct used aiohttp; routed waited for buffered EOF).
- Wire cases cover mixed-case, missing, and empty SSE Content-Type, JSON bodies
  exceeding the SSE limit, raw HTTP errors, terminal SSE errors, truncated/empty
  terminal lifecycles, oversized events, active partial chunks, idle and total
  deadlines, and optional total timeouts.
- A refused first proxy advances to the selected endpoint and preserves exact
  route metadata. An accepted POST with a body failure never reaches the next
  endpoint or Python fallback. Missing helpers retain Python parsing and raw
  error interpretation even when JSON errors use text/plain.
- Cancellation inside an already cancelled AnyIO scope closes the native
  request and owned routed session while a peer request completes. A separate
  deterministic pre-head regression proves stream unregistration; this failed
  before the adapter cleanup fix.
- The existing Rust oversized-event terminal test exposed an EOF/cancel race.
  Completed exchanges now win simultaneous cancellation; the regression passed
  20 consecutive runs after the fix.

## Checks

With `CODEX_LB_NATIVE_EGRESS_TEST_BINARY` pointing to the release worker:

```sh
python -m pytest -q --timeout=30 \
  tests/unit/test_native_egress.py tests/unit/test_codex_client.py \
  tests/unit/test_codex_upstream_paths.py tests/unit/test_native_egress_packaging.py \
  tests/unit/test_native_sse_fixtures.py tests/unit/test_sse.py \
  tests/integration/test_native_sse_egress.py tests/integration/test_native_routed_egress.py
# 276 passed

python -m pytest -q --timeout=30 tests/unit/test_proxy_utils.py \
  tests/integration/test_proxy_responses.py -k compact
# 111 passed, 1340 deselected

make rust-check
# fmt, Clippy (-D warnings), 19 tests, locked release build passed

make rust-audit
# advisories, bans, licenses, sources passed; existing duplicate-version warnings

openspec validate --specs --strict
# 58 passed; change delta also passed strict validation
```

Ruff check/format, Ty on changed Python files, proxy architecture,
cancellation safety, timing seams, and git diff checks passed.
No dependency or lockfile changes. Full repository/cloud CI, deployment, and
performance measurement are outside this local verification.
