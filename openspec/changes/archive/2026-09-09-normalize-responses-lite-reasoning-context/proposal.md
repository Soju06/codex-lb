## Why

Production telemetry in issue #1411 records 273 Responses Lite requests rejected
by upstream because the proxy emitted the canonical Lite signal without the
required `reasoning.context = "all_turns"` body contract. The proxy already owns
Lite signaling across HTTP, compact, websocket, bridge, and replay paths, so it
must make the signaled payload internally consistent before egress.

## What Changes

- Normalize every final request that carries the proxy-derived Responses Lite
  HTTP header or trusted websocket metadata marker so its reasoning object has
  `context = "all_turns"`.
- Preserve reasoning effort, summary, and unknown sibling fields; create the
  reasoning object when absent or null, and replace any incompatible context
  value only for a request that is actually forwarded as Lite.
- Apply the same contract to Responses, compact, direct websocket, HTTP bridge,
  websocket-to-HTTP fallback, and trusted Lite continuity/replay preparation.
- Leave non-Lite reasoning payloads and the existing Lite classification and
  continuity trust rules unchanged.
- Add transport- and product-path regression coverage for omitted, conflicting,
  and already-canonical reasoning context.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `responses-api-compat`: Extend the existing body-derived Responses Lite
  signaling contract with the required canonical reasoning context on every
  signaled upstream payload.

## Impact

- Affected code: Responses/compact request serialization and the shared
  Responses Lite HTTP/websocket egress helpers in `app/core/clients/proxy.py`
  and `app/modules/proxy/_service/`.
- Affected tests: proxy Responses and compact integration tests, direct
  websocket tests, HTTP bridge tests, and focused Lite helper/transport tests.
- No public endpoint, response schema, database schema, setting, or dependency
  changes. Non-Lite requests retain their current wire representation.
