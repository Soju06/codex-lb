## Why

Late upstream bridge failures can be converted into `response.failed` after the HTTP response has already started. Those converted events did not carry a response id, so the public Responses SSE normalizer synthesized an id-less `response.created` event before the failure. SDK clients can fail initial stream parsing before exposing the terminal upstream error.

## What Changes

- Converted streaming `ProxyResponseError` failures include the active request id in the generated `response.failed` event.
- Public `/v1/responses` streams synthesize `response.created` from that id-bearing failure so clients can parse the stream and observe the terminal error.
- HTTP bridge websocket/session failures that occur before downstream-visible output switch the affected bridge key to raw HTTP fallback and replay the request over HTTPS, matching Codex CLI's fallback policy.

## Impact

- Affects streaming Responses API error forwarding only.
- Affects HTTP responses session bridge transport selection after pre-visible websocket failures.
- Does not change non-streaming error JSON envelopes or successful streams.
