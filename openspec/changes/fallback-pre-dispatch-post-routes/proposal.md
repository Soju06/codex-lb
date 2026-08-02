# Fail Over Pre-Dispatch Routed POST Requests

## Summary

Allow a routed HTTP request to use the next endpoint in its configured pool when the current endpoint fails before upstream dispatch is possible.

## Why

The routed Codex client currently permits pool fallback only for idempotent methods. A configured HTTP proxy can reset the TLS connection before response headers while a streaming `POST` is being opened. In that case no request reached ChatGPT, and the client already records `retryable_same_contract=True`, but the POST is still returned as an upstream failure instead of using its configured fallback endpoint.

## What Changes

- Permit same-pool fallback for non-idempotent routed HTTP requests only when typed transport provenance proves the request was never dispatched.
- Keep fallback disabled for response/body failures, stream failures, and TLS verification failures because those do not establish the same safe replay contract.
- Preserve route metadata so a successful fallback records the endpoint actually used and `fallback_used=True`.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `upstream-proxy-routing`: pre-dispatch transport failures may safely fail over across the configured pool for all HTTP methods.

## Non-Goals

- No direct-egress fallback when routed endpoints fail.
- No replay after upstream response headers or request dispatch.
- No changes to the configured proxy endpoints, pool ordering, or default 8800 route.
