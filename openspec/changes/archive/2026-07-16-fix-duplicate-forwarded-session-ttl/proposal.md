## Why

When the loopback-host-header override is enabled, dashboard session TTL enforcement inspects only the first value of each forwarded client-IP header. A remote request can therefore place an empty field first and a non-empty duplicate second, receiving the configured long session lifetime instead of the required 12-hour cap.

## What Changes

- Inspect every field value for each forwarded client-IP header before granting a long dashboard session through the loopback-host-header override.
- Add a regression proving that a later non-empty duplicate forces the 12-hour remote-session cap.
- Clarify the admin-auth contract for repeated forwarded client-IP fields.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `admin-auth`: Require the long-session loopback-host-header override to reject a request when any value of any forwarded client-IP field is non-empty, including repeated fields.

## Impact

- Affected code: `app/core/auth/dashboard_session_ttl.py`.
- Affected tests: `tests/unit/test_dashboard_session_ttl.py`.
- Compatibility: Requests without forwarded client-IP fields and requests whose every such field value is empty retain existing behavior. Requests that relied on a later non-empty duplicate being ignored are intentionally capped at 12 hours.
- Dependencies and persisted data: None.
