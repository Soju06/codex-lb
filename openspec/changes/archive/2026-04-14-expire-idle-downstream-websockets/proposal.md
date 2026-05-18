# Expire Idle Downstream Websockets

## Why

The Responses websocket proxy currently holds a `proxy_websocket` bulkhead slot for the entire downstream websocket lifetime. If a client leaks idle downstream websocket sessions or reconnects aggressively without cleaning up older sockets, those abandoned sessions can monopolize the websocket lane and cause repeated local `429` handshake rejections even when the upstream is healthy.

The proxy already times out stalled upstream response streams, but it does not reclaim downstream websocket sessions that have no pending requests and no client traffic. That leaves the proxy vulnerable to long-lived idle sockets from one client process saturating the lane for everyone else.

## What Changes

- Add a configurable downstream websocket idle timeout for Responses websocket routes.
- Close downstream websocket sessions after that timeout only when they have no pending requests.
- Reclaim the associated upstream websocket session and downstream admission slot when an idle downstream websocket expires.
- Add regression coverage for the idle-expiry behavior and the new runtime setting.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: define idle expiry behavior for downstream Responses websocket sessions so abandoned client sockets do not hold capacity indefinitely.

## Impact

- Code: `app/core/config/settings.py`, `app/modules/proxy/service.py`
- Tests: `tests/integration/test_proxy_websocket_responses.py`, `tests/unit/test_settings_multi_replica.py`
