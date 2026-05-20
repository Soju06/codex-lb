## Why

Transient upstream websocket closes can trigger transparent replay for a pre-created Responses request. Replaying on the same account that just dropped the upstream socket can repeat the same account-local instability and turn a recoverable fresh request into another `stream_incomplete`.

## What Changes

- Record a transient upstream stream error for the account that drops a replayable websocket request.
- Route fresh websocket replay reconnects away from accounts that already failed that replay attempt.
- Preserve previous-response affinity by not excluding the known owner account for requests that still depend on `previous_response_id`.
- Add unit coverage for fresh replay exclusion and previous-response owner preservation.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `responses-api-compat`: Websocket replay routing now excludes failed accounts for fresh replay attempts while preserving previous-response owner affinity.

## Impact

- Affects upstream websocket replay handling in `app/modules/proxy/service.py`.
- Adds focused unit tests in `tests/unit/test_proxy_utils.py`.
- No public API, schema, dependency, or configuration changes.
