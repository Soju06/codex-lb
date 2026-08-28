# WebSocket Connect Error Preservation Context

## Purpose and scope

The normative routing contract is in the delta for
[`upstream-proxy-routing`](specs/upstream-proxy-routing/spec.md). This change is
limited to the lifecycle of the context returned by the routed Codex WebSocket
client. It does not change endpoint selection, retry eligibility, health
settlement, or downstream error envelopes.

## Decision and alternatives

The client records successful context entry and invokes `__aexit__` only for a
context it actually entered. This mirrors Python's `async with` protocol:
`__aexit__` is not called when `__aenter__` fails. Removing all cleanup from the
error path would also avoid the reported crash, but would make ownership
ambiguous if a later operation ever fails after successful entry. Catching and
discarding the cleanup `AttributeError` was rejected because it would encode an
aiohttp implementation detail and could hide other lifecycle defects.

## Constraints and failure modes

- The original failure must still be converted to a credential-safe
  `CodexTransportError`; proxy credentials and raw route URLs must not appear in
  the message.
- Handshake-status and network-error fallback controls keep their existing
  semantics.
- A failed `__aenter__` owns its partial-resource cleanup, as required by the
  asynchronous context manager contract.
- Successfully opened WebSockets retain their current caller-owned context and
  close behavior.

## Example

An HTTP proxy restart causes aiohttp's awaitable WebSocket request context to
raise `ClientProxyConnectionError` before it stores a response in `_resp`.
codex-lb must classify that connection failure and either try the next endpoint
or return a credential-safe transport error. It must not call `__aexit__` on
the unentered context and replace the failure with an `_resp` `AttributeError`.

## Operational notes

No rollout setting or migration is required. After deployment, upstream proxy
disconnects may still fail requests, but logs and callers will receive the real
classified connection failure instead of a cleanup crash, making ordinary
network recovery and diagnostics effective again.
