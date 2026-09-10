## Why

The loopback OAuth callback server's default access logger records the complete request target, exposing temporary authorization codes and anti-CSRF state tokens in both supported log formats. The callback must remain functional without sending credential-bearing query text to log sinks.

## What Changes

- Prevent the callback-only server from emitting raw HTTP access records.
- Preserve callback routing, response status, and OAuth completion behavior.
- Add a real-server regression covering authorization-code and state sentinels.

## Capabilities

### New Capabilities

- `oauth-callback-privacy`: Defines credential-safe logging behavior for the loopback OAuth callback boundary.

### Modified Capabilities

None.

## Impact

- Affects the callback-only `aiohttp` runner in `app/modules/oauth/service.py` and its integration coverage.
- Removes one generic callback access record; existing application-level OAuth outcome logs remain unchanged.
