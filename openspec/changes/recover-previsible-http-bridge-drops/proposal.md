## Why

Intermittent upstream Responses websocket closes can end HTTP bridge turns before
`response.completed`. Codex CLI and OpenCode can retry whole sampling requests,
but codex-lb should recover locally when it can prove that replay will not
duplicate downstream-visible output or tool effects.

## What Changes

- Define replay-safe recovery for HTTP bridge websocket drops before visible
  downstream output.
- Preserve fail-closed behavior after visible output has been emitted.
- Add diagnostics for retry skip reasons so operators can distinguish unsafe
  replay from transport or capacity failures.

## Impact

- Affects HTTP `/v1/responses` and `/backend-api/codex/responses` requests that
  use the HTTP responses session bridge.
- Does not add upstream idempotency or in-flight resume semantics; unsafe cases
  still surface retryable `stream_incomplete` for clients to handle.
