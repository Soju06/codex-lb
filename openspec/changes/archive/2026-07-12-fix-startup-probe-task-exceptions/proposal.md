## Why

Startup error probes for Responses and chat-completions streams keep reading the
first upstream item in a shielded task after the probe timeout expires. If the
owning request path abandons the returned stream before consuming that task, an
upstream `ProxyResponseError` can surface later as an unhandled asyncio task
exception.

## What Changes

- Ensure timed-out startup probe tasks have their eventual exception consumed if
  the returned stream is abandoned.
- Preserve the existing behavior for callers that do consume the returned stream:
  the first item, `StopAsyncIteration`, or `ProxyResponseError` is still observed
  through iteration.
- Add regression coverage for abandoned startup probe streams.

## Impact

- Removes noisy and misleading asyncio diagnostics for upstream first-item
  failures.
- Keeps client-visible startup error and streaming behavior unchanged.
