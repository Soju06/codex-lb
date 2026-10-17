## Why

Codex-rs does not yet emit `async: true` tool calls (`rust-v0.153.4` /
`openai/codex`). The Responses protocol already documents them. Without
tracking, an intervening anchored turn synthesizes interrupted outputs for
those calls and drops the real delayed result.

## What Changes

- Preserve pending async `function_call` / `custom_tool_call` identities
  across anchored continuations on WebSocket and HTTP-bridge.
- Do not inject synthetic interrupted outputs for known async calls.
- Complete a pending async call only when a matching typed output arrives.
- Explicitly document that this is protocol-forward, not current Codex-rs
  client behavior.

Out of scope: `configuration_update` / Ultra (#2097), WebSocket
`response.steer`, reservation extend/reduce / `FOR UPDATE`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: async tool results remain pending across
  continuations; interrupted-tool repair skips known async calls.

## Impact

- Code: WebSocket and HTTP-bridge continuity state, interrupted-output
  injection, completion bookkeeping.
- Tests: unit intervening-turn WS coverage; HTTP-bridge integration.
- Docs: OpenSpec only. No settings, schema, or dashboard changes.
