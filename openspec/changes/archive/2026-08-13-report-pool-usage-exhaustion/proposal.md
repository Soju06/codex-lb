## Why

When every account eligible for a Responses request is exhausted by known pool
usage windows, codex-lb can currently collapse the selection failure into a
generic no-account/server-unavailable response. That hides the user-actionable
upstream condition from Codex/OpenAI-compatible clients and makes agents treat a
quota window as infrastructure failure.

## What Changes

- Preserve the stable `usage_limit_reached` code from account selection when the
  whole eligible pool is exhausted by usage windows.
- Return HTTP `429` with an OpenAI-style error envelope whose
  `error.code` and `error.type` are both `usage_limit_reached`.
- Preserve the selected reset hint as `error.resets_at` when account selection
  has one, and use the same contract across HTTP, streaming, bridge, and
  WebSocket selection-failure paths.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: define the externally visible Responses error
  contract for pool-wide usage exhaustion.

## Impact

- Affected code: account selection failure mapping and Responses proxy surfaces.
- Affected APIs: failure status/body for pool-wide usage exhaustion changes from
  generic unavailable/no-account semantics to HTTP 429 `usage_limit_reached`.
- Configuration and schema: no changes.
