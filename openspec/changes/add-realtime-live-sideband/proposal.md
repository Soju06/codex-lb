## Why

Codex-LB already forwards Codex's subscription-backed WebRTC call creation request, but the returned call is account-bound and Codex immediately opens a second Frameless sideband WebSocket at `/v1/live/{call_id}` using the same ChatGPT identity. Codex-LB exposes no corresponding WebSocket route and records no durable call-to-account ownership, so a client cannot complete the interaction through a pooled deployment without either bypassing the proxy or risking a different account on the sideband.

## What Changes

- Capture the final pooled account that successfully creates a realtime call and bind the upstream `rtc_...` call id to that account under the authenticated API-key scope.
- Persist only a bounded hash of API-key scope plus call id in the existing sticky-session store, with fixed expiry and opportunistic cleanup; never persist the bearer token, SDP, attestation, or frame payloads.
- Expose authenticated `WS /v1/live/{call_id}` forwarding to OpenAI's subscription-backed Frameless sideband endpoint.
- Resolve the exact bound account, enforce API-key account assignment, reserve that account's stream capacity, and fail closed rather than selecting or retrying another account.
- Replace downstream proxy credentials with the bound account OAuth identity while preserving the Codex session, originator, alpha-protocol, and attestation headers needed by the upstream handshake. Do not add the Responses-WebSocket beta header.
- Relay text, binary, close, and error semantics bidirectionally without interpreting realtime frames or adding payload logging.
- Preserve existing refresh and failover behavior for the pre-visible HTTP call-creation request. The sideband attach itself neither refreshes tokens nor fails over because its call id is already account-owned.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Add subscription-backed Codex GPT-Live/Frameless call-owner continuity and sideband WebSocket forwarding.

## Impact

The change affects Codex control-request success metadata, the `/backend-api/codex/realtime/calls` route adapter, sticky-session repository cleanup, WebSocket upstream connection helpers, proxy service composition, and focused unit/integration coverage. It adds no setting, dependency, public model-catalog entry, token refresh, account credential mutation, or claim that `/v1/live` is the documented public Realtime API.
