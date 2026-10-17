## Why

Emergency fixes #1969 and #1992 moved cancellation-deferring waits onto the canonical shared-future primitive, but two HTTP bridge cleanup windows remain. Group terminal barriers are marked released before their await completes, and detached-session capacity finalization runs inline after resource close. Cancellation in either window can strand grouped siblings or leave a closed detached generation consuming capacity.

## What Changes

- Give terminal append and delivery barriers one strongly owned task each, re-await that owner after cancellation, and propagate cancellation only after terminal delivery and barrier ordering complete.
- Give detached-session registry finalization its own task so cancellation while waiting for the registry lock cannot strand a closed generation.
- Add product-path regressions for cancellation during grouped terminal persistence and detached ownership finalization.
- Add an AST repository check that rejects cancellation-catching retry loops around `asyncio.shield()`, including loops nested inside an AnyIO shield; the canonical `wait_on_shared_future()` primitive is the supported replacement.
- Drop the original PR's cancellation primitive, API-key, Compact, database teardown, streaming, and helper-body changes because current `main` already provides them through #1969 and #1992.
- No public API, configuration, persistence schema, or wire-format changes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Require grouped terminal barriers and detached bridge-session ownership finalization to complete under cancellation before the caller's cancellation propagates.
- `proxy-architecture`: Require the repository architecture gate to reject cancellation-catching `asyncio.shield()` retry loops, including conditional retry paths, without leaking nested import aliases across the module.

## Impact

Affected code is limited to HTTP bridge terminal persistence, bridge-session close finalization, focused regressions, and the repository architecture gate. The implementation reuses `app/core/utils/shared_future.py`; it introduces no second cancellation primitive. Contributor attribution is already present on current `main` through merged PR #1877.
