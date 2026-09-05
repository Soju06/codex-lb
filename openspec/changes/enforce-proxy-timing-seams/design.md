## Context

`scripts/check_proxy_architecture.py` and `scripts/check_cancellation_safety.py`
are the two existing proxy fitness gates. Both are stdlib-only AST scripts, run
by `make architecture-check`, and the first one loads its numeric ratchets from
a marked TOML block inside `openspec/specs/proxy-architecture/spec.md` so a
threshold move is a reviewable spec edit rather than a Python constant. The
timing-seam gate follows the same house style and reuses the same spec file
with a second, independently marked block, so the thresholds loader (which
requires exactly one of *its* marker pair and rejects unknown keys) is
untouched.

## Goals / Non-Goals

**Goals:**

- Fail `make lint` when a new raw timing, task-spawn or clock site lands in a
  proxy turn-lifecycle module, or when a caller of an owner-less seam function
  silently takes its real default.
- Keep every exemption reviewable in one place and make headroom impossible to
  accrue silently.
- Let a reviewer distinguish a missed injection from an accepted residual
  (`--explain`) and regenerate the table mechanically (`--report`).

**Non-Goals:**

- Proving that the injected sites are *used* correctly (that is the harness's
  property test and the real-parity tests in PR A).
- Scanning `app/core/clients/proxy.py`, `app/core/utils/sse.py` or other
  modules outside the proxy package; `app/core/clock.py` is the one permitted
  raw adapter and is excluded by construction.
- Detecting timing primitives reached through arbitrary objects (`self._loop`,
  a loop passed as a parameter). Only loops obtained from
  `asyncio.get_running_loop()`/`get_event_loop()` in the same scope resolve.

## Decisions

### Scan every proxy module with per-module allowances, not a module list

An allowlist of scanned modules would let a new lifecycle module opt out by
omission. Scanning every `*.py` under `app/modules/proxy/` (plus
`shared_future.py`, whose `ensure_future` fallback and timed
`wait_on_shared_future` are turn-path seams) with unlisted modules at zero
means a raw site anywhere in the package fails unless the spec block is
edited in the same diff. A listed module that no longer exists is a
configuration failure so the gate cannot pass silently after a rename.

### Exact-count pin in the test, `<=` in the script

The script keeps the ratchet semantics of the other gates (a count below its
allowance passes). `test_repository_allowances_are_exact` additionally asserts
`count == allowance` for every listed module and that no unlisted module has
a count, so removing a raw site must lower the number in the same diff and a
stale allowance cannot hide a later regression under accrued headroom.
`test_repository_report_reproduces_committed_block` pins that `--report`
regenerates the committed block byte-for-value.

### `missing-scheduler-kwarg` closes the hidden-default hole

Every owner-less seam function keeps a real default so partial test doubles
work, which means a new caller can silently take the wall clock. The rule
matches the callee by its terminal attribute name (`_facade()._x(...)`,
`self._x(...)` and bare `_x(...)` all resolve) and requires each configured
keyword; the value is a string or a list so functions that take both
`scheduler` and `clock` require both. Passing an explicit
`REAL_SCHEDULER`/`REAL_CLOCK` is allowed because it is visible and greppable.
A `**kwargs` splat is taken as satisfying the requirement because the
keywords cannot be inspected statically. The `api.py` route layer's nineteen
calls to the deferring-cancellation helpers are carried as its timing
allowance rather than exempted by rule, so the owner can make them explicit
and lower the number later.

Functions whose name is shared with an unrelated method (`stream_responses`
exists on both the HTTP bridge owner client and `ProxyService`;
`_select_with_stickiness` is both a module function and a `LoadBalancer`
method) are deliberately not listed: terminal-name matching would flag the
homonym.

### `raw-clock-read` is a separate allowance table

Clock reads are the highest-volume, most-likely-to-rot rule and the one the
owner may want to strike or defer independently; a separate table lets that
happen in one edit without touching the timing rows. The rule counts the
`time.time() if now is None else now` and `REAL_CLOCK.x()` default idioms on
purpose so a new hidden wall-clock default shows up as a raised number.

### Alias discovery is file-wide and canonical names are always recognised

`check_cancellation_safety.py` restricts alias discovery to module-level
imports so an unrelated nested import cannot redefine an alias for the whole
file. For a rot guard the failure modes are asymmetric: a false positive is
visible and fixable by renaming, an evasion through a function-local
`import asyncio as aio` is silent. This gate therefore collects aliases from
every import in the file and always recognises the canonical
`asyncio`/`anyio`/`time` names; a function parameter that shadows a recognised
name still suppresses the match, as in the other gate.

### Configuration failures do not hide parse failures

As with the thresholds block, an invalid definition (missing or duplicate
marker, wrong fence, malformed TOML, unknown key or table, non-positive or
non-integer allowance, malformed keyword list) is reported by name. Module
parse failures are still reported alongside it; allowance comparisons are not,
because they cannot be evaluated without a valid definition.

## Risks / Trade-offs

- Rename churn: a module rename must update the block in the same diff. This
  is intended (the gate refuses to pass silently when its target disappears).
- Terminal-name matching for `missing-scheduler-kwarg` can flag an unrelated
  method with the same name; the table is explicit and small, and the two
  known homonyms are excluded.
- The CI `changes` filter runs `make lint` for `app/**`, `scripts/**`,
  `tests/**` and `Makefile`; an edit that touches only the spec block is
  validated on the next backend change. This matches the thresholds block.

## Open Questions

- Whether the `api.py` route-layer calls should pass `REAL_SCHEDULER`
  explicitly (allowance 19 -> 0) or stay as allowance.
- Whether the compact endpoint, realtime relay, request-log drain, event
  batcher and upstream client residuals get their own injection follow-ups or
  remain accepted allowances.
