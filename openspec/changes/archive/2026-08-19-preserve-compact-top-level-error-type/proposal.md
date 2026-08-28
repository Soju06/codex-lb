## Why

The compact Responses transport derives the correct HTTP status from a
top-level terminal SSE frame's `error_type`, but its fallback OpenAI envelope
replaces that supplied type with `server_error`. Clients therefore receive a
contradictory response such as HTTP 400 with `error.type=server_error` even
though the upstream classified the failure as `invalid_request_error`.

## What Changes

- Preserve a non-empty top-level compact SSE `error_type` in the emitted OpenAI
  error envelope.
- Keep `server_error` as the compatibility fallback when the top-level field is
  absent or blank.
- Preserve existing nested error-envelope behavior and status, code, message,
  and parameter mapping.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: define compact terminal error-envelope behavior for
  top-level `type=error` SSE frames.

## Impact

- Affects the compact SSE terminal-error converter in
  `app/core/clients/proxy.py`.
- Adds focused routed transport tests and fallback/nested controls.
- Does not change request routing, retry behavior, account health, schemas,
  settings, dependencies, or non-compact Responses behavior.
