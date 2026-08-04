## Context

`aiohttp.ClientSession.ws_connect()` returns an awaitable asynchronous context
manager. Its internal response is initialized only after the connection
coroutine succeeds. `CodexClient.open_ws_with_route_metadata()` currently keeps
that unentered manager in `context`; when awaiting it raises, the shared error
path calls `context.__aexit__()` and aiohttp fails while accessing the missing
response. The cleanup exception bypasses the existing credential-safe transport
classification and route fallback logic.

## Goals / Non-Goals

**Goals:**

- Follow asynchronous context manager ownership semantics during WebSocket
  connection setup.
- Preserve the original connect or handshake failure for existing transport
  classification and fallback controls.
- Retain current cleanup behavior for any context that was entered
  successfully.

**Non-Goals:**

- Change endpoint selection, account selection, retry budgets, or health
  penalties.
- Special-case aiohttp's private `_resp` attribute or suppress arbitrary
  cleanup failures.
- Change successful WebSocket ownership or close behavior.

## Decisions

Track context entry explicitly for each route attempt. Set the entered context
only after `__aenter__()` returns successfully, and invoke `__aexit__()` from
the error path only when that marker is present.

This follows the behavior of `async with`, which does not call `__aexit__` when
`__aenter__` raises. It is preferable to checking for `_resp`, because `_resp`
is an aiohttp implementation detail, and preferable to deleting cleanup
entirely, because explicit ownership remains correct if later code gains a
failure point after successful entry.

The regression uses an in-memory aiohttp-style awaitable context manager. Its
connection coroutine raises without opening a socket, and its `__aexit__`
method records any invalid cleanup attempt. This exercises the public routed
client behavior without relying on an external network.

## Risks / Trade-offs

- A context manager with a broken `__aenter__` that expects its caller to invoke
  `__aexit__` could retain partial state. Such a contract is incompatible with
  Python's context manager protocol; the regression intentionally follows the
  standard protocol used by aiohttp.
- Entry state adds one local variable per route attempt. Keeping it local avoids
  sharing ownership across fallback endpoints.
- Errors raised by cleanup after a genuinely successful entry retain normal
  context manager behavior and may supersede a later error; there is currently
  no throwing operation between successful entry and returning the result.

## Migration Plan

No migration or feature flag is required. Deploy as a patch-level client
lifecycle correction. Rollback is the ordinary code rollback.

## Open Questions

None.
