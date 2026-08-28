## Why

When an aiohttp WebSocket connection attempt fails before its request context
manager has been entered, the Codex client calls that context manager's
`__aexit__` method anyway. aiohttp then raises an `AttributeError` that masks
the real connection failure and prevents the normal routed transport error
classification and fallback behavior.

## What Changes

- Track whether an upstream WebSocket context manager was entered successfully
  before attempting to exit it during error cleanup.
- Preserve the original connection failure for credential-safe
  `CodexTransportError` classification and configured same-pool fallback.
- Add regression coverage for an awaitable aiohttp-style context manager whose
  connection coroutine fails before entry completes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `upstream-proxy-routing`: Routed WebSocket connection failures remain
  observable as the original credential-safe transport failure and follow the
  configured fallback policy.

## Impact

- `app/core/clients/codex.py`
- `tests/unit/test_codex_client.py`
- No public API, configuration, dependency, database, or dashboard changes.
