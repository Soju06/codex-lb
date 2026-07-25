## Why

Codex-LB already forwards the Codex app's subscription-backed WebRTC call-creation request, but a successful call is account-bound and the app immediately opens a second control sideband WebSocket with the same ChatGPT identity. Current upstream main neither records the final account that created the call nor exposes the installed-app, v3, and legacy sideband ingress forms, so a pooled deployment can create a call that it cannot safely join. The backend must also keep its internal ownership rows out of operator-facing sticky-session surfaces, while the existing dashboard must accept the `realtime_live` request-log kind the sideband emits.

## What Changes

- Forward the authorized installed-app `POST /backend-api/codex/realtime/calls` endpoint only for registered proxy API keys, independently of the global ordinary-proxy auth toggle.
- Capture the final account that successfully creates a call after any pre-visible refresh or failover, then bind a bounded `rtc_...` or canonical UUID call id immutably under that API-key scope.
- Persist only a bounded ownership digest in a reserved sticky-session namespace, with fixed expiry and throttled bounded cleanup; hide and protect reserved rows from ordinary dashboard list and delete operations.
- Expose authenticated `WS /backend-api/codex/{call_id}`, `WS /v1/live/{call_id}`, and `WS /v1/realtime?call_id={call_id}` adapters through one exact-owner service while preserving v3 live-path and legacy ordered-query upstream protocols.
- Fresh-load the bound owner, enforce current API-key assignment and stream capacity, and forbid refresh, account fallback, or definitive-denial replay after a call id exists.
- Relay text, binary, close, and error semantics without inspecting or archiving realtime frames; suppress call SDP from tracing and keep live errors credential-safe without changing ordinary Responses WebSocket error behavior.
- Extend the existing dashboard request-log parser to accept persisted `requestKind: "realtime_live"` WebSocket rows. This fixes an existing producer/consumer contract; it adds no dashboard navigation, setting, or new feature surface.
- Treat the related SDK-documented `POST /v1/realtime/calls` and `POST /v1/realtime/client_secrets` endpoints as protocol-family context only; this private app-compatibility change intentionally exposes neither public endpoint.

## Capabilities

### New Capabilities

- `realtime-api-compat`: Subscription-backed Codex call-owner continuity, protocol-faithful private sideband forwarding, reserved ownership isolation, and dashboard consumption of its request logs.

### Modified Capabilities

None.

## Impact

The change touches Codex control success observation, dedicated call and sideband routing, sticky-session persistence and operator filtering, upstream WebSocket construction, proxy service composition, request logging, and the existing Recent Requests response parser. The private feature uses existing configuration and becomes available only to a registered proxy key; an untouched base proxy and dashboard still start with zero new setup. It adds no setting, migration, dependency, public model entry, dashboard navigation, README section, `.env.example` line, or public `/v1/realtime/calls` or `/v1/realtime/client_secrets` implementation. A published docs page and synced main `realtime-api-compat` capability describe the private boundary.
