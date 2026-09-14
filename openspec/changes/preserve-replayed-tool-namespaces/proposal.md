## Why

The shared Responses serializer removes `namespace` from historical tool-call
items before sending them to ChatGPT. That metadata is part of the executor
identity, so a long-lived or compacted conversation can turn a call such as
`mcp__cua_repl.js` into an unrelated generic function and lose Computer/Chrome
tool routing. OpenAI-compatible model sources still need the old compatibility
normalization, so the two wire boundaries must be separated.

## What Changes

- Preserve `namespace` and `name` together for recognized replayed tool-call
  input items on standard Responses, Responses-Lite/compact, HTTP bridge, and
  upstream WebSocket ChatGPT paths.
- Keep the account-neutral replay classifier and local deduplication operating
  on the original namespaced history.
- Retain namespace stripping only at the configured OpenAI-compatible
  model-source dispatch boundary.
- Add route-level, WebSocket, compact, and serializer regression coverage plus
  an OpenSpec contract for the boundary.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: change the namespace-normalization requirement so
  ChatGPT wire paths preserve executor identity while model-source forwarding
  remains sanitized.

## Impact

The change affects `app/core/openai/requests.py`, the model-source dispatch
boundary in `app/modules/proxy/api.py`, and the corresponding unit and
integration tests. It changes only request-history metadata projection; tool
declarations, account ownership checks, replay safety, and source-compatible
field forwarding remain otherwise unchanged.
