## Why

An HTTP bridge retry after `response.created` can hide a replacement upstream
response behind the original client-visible id. The durable bridge store can
route that id to its session, but it does not retain an old-id-to-replacement-id
target for the next upstream `previous_response_id` request.

## What Changes

- Permit HTTP bridge replay only before `response.created` assigns an upstream
  response id.
- Forward terminal errors after `response.created` instead of migrating the
  request to another account under the old downstream id.

## Impact

- Affected capability: `responses-api-compat`.
- The bridge fails safely at the client-visible response-id boundary; direct
  WebSocket sequence handling is unchanged.
