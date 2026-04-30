## Why

Recent continuity fixes closed the main websocket and HTTP fallback leaks, but two edge cases still violate the intended contract. Bridge-enabled HTTP can still return raw `400 previous_response_not_found` when continuity metadata is lost, and owner/ring lookup failures can still degrade into local recovery without guaranteed owner pinning.

## What Changes

- Replace bridge-local continuity-loss `previous_response_not_found` responses with retryable continuity errors.
- Require hard continuity requests to fail closed when owner or ring lookup errors prevent safe pinning.
- Add regression coverage for bridge continuity-loss and lookup-failure edge cases.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `responses-api-compat`: continuity-dependent follow-up requests now use retryable fail-closed errors instead of raw `previous_response_not_found`, and owner lookup failures no longer degrade into unpinned recovery.

## Impact

- Affected code: `app/modules/proxy/service.py`, `app/modules/proxy/api.py`, `app/core/clients/proxy.py`, and continuity-focused tests.
- Affected APIs: HTTP `/v1/responses`, HTTP `/backend-api/codex/responses`, and websocket Responses continuity handling.
- Operational impact: continuity failures become consistently retryable; operators should watch `stream_incomplete` and `upstream_unavailable` instead of raw `previous_response_not_found` for these paths.
