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
  CI reproduced it after the initial poll-order fix passed 20 local runs.
  With two Tokio worker threads, the initial fix failed on local iteration 10.
  Terminal emission now happens outside the cancellable HTTP future, including
  stdout flush. The final code passed 100 consecutive protocol suites (600
  test executions) with two Tokio worker threads; the suite checks no extra
  terminal after successful SSE, raw HTTP errors, oversize, and idle timeout.

## Checks

With `CODEX_LB_NATIVE_EGRESS_TEST_BINARY` pointing to the release worker:

```sh
python -m pytest -q --timeout=30 \
  tests/unit/test_native_egress.py tests/unit/test_codex_client.py \
  tests/unit/test_codex_upstream_paths.py tests/unit/test_native_egress_packaging.py \
  tests/unit/test_native_sse_fixtures.py tests/unit/test_sse.py \
  tests/integration/test_native_sse_egress.py tests/integration/test_native_routed_egress.py
# 288 passed (including 8 media-type and 4 compact idle-budget regressions)

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

## CI follow-up verification

The native-terminal correction was checked with `make rust-check` and the
following real-worker probes on the rebuilt debug worker:

```sh
python -m pytest -q --timeout=30 tests/integration/test_native_sse_egress.py \
  tests/integration/test_native_routed_egress.py
# 57 passed
```

CI failure evidence for the superseded poll-order fix:
[Rust workspace job](https://github.com/Soju06/codex-lb/actions/runs/34193031991/job/101954940010).

Review also identified substring-based Content-Type detection. Eight added
cases failed before the fix: `text/event-stream+json` and a JSON media-type
parameter containing `text/event-stream`, across native/missing-helper and
direct/routed transports. Exact media-type comparison and content-type-independent
compact JSON decoding now preserve all eight payloads without SSE scanning or
event byte limits. The final 288-test native/client suite, 111 compact tests,
`make rust-check`, Ruff/Ty, architecture checks, and strict specs all passed.
Both compact requirements now explicitly require negotiated capability support;
missing helpers may fall back, while incompatible installed helpers fail closed.

Four additional direct/routed, native/missing-helper cases verify the existing
compact idle-budget policy: a configured 1-second compact timeout permits a
150-ms upstream body gap even when the ordinary stream idle timeout is 50 ms.
The pre-migration Python implementation uses the same compact-timeout-first
rule. The compact native integration subset passed 46 tests, and the complete
288-test native/client suite passed against main including #2166. No runtime
timeout or upstream scheme policy changed in response to those review notes.
