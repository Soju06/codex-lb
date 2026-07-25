## Context

Codex Live Voice has two account-coupled control legs: an HTTP WebRTC call-creation request and a sideband WebSocket. The generic Codex control proxy may refresh or fail over before a response becomes visible, so only the final successful account owns the returned call. An installed Codex app derives `WS /backend-api/codex/{call_id}` from a returned `/v1/realtime/calls/{call_id}` location. First-party Codex source also distinguishes v3 `/v1/live/{call_id}` from legacy v1/v2 `/v1/realtime?...&call_id={call_id}`. These are private compatibility routes, not the documented public Realtime API.

Codex-LB already has durable sticky mappings, account assignment policy, stream leases, direct/proxied egress, request logs, and a dashboard Recent Requests view. The design reuses those seams without a new setting, migration, dependency, scheduler, navigation item, or public model.

## Goals / Non-Goals

**Goals:**

- Bind the sideband to the final account that successfully created the call.
- Scope call possession to the registered proxy key that created it.
- Work across replicas with bounded durable ownership and cleanup.
- Support current-app, v3, and legacy ingress through one deep service.
- Preserve protocol-specific URLs, ordered query parameters, and supplied handshake context while replacing credentials with the bound owner.
- Make cancellation, close, request logging, and resource ownership deterministic.
- Keep reserved ownership out of ordinary operator sticky-session operations.
- Keep `realtime_live` WebSocket request rows consumable by the existing dashboard.

**Non-Goals:**

- Public GPT-Live or public `POST /v1/realtime/calls` support.
- WebRTC media relay or SDP/frame/transcript/audio logging.
- Realtime event translation, parsing, authorization, or speculative subprotocol synthesis.
- A setting, migration, dependency, model-catalog entry, README/docs addition, dashboard navigation item, or new setup step.
- Any change to ordinary Responses WebSocket behavior.

## Decisions

### Require an existing proxy key without adding setup

The private call-create and sideband routes always resolve a registered proxy API key, even when ordinary proxy routes run with authentication disabled. Missing or invalid keys fail before account selection or upstream contact. This makes the private feature unavailable until an operator creates a key, but adds no required configuration: the base proxy and dashboard still work untouched, satisfying P1 through zero-config base behavior rather than by weakening private-route authorization.

A dedicated call-creation router is registered before the generic Codex router so the generic auth dependency cannot shadow this stricter route contract.

### Capture only the final successful control account

`codex_control_request` reports the final account id through a synchronous success observer on every successful return path, including initial success, pre-visible failover, and forced-refresh success. The route binds ownership after the control request returns, outside upstream account-penalty handling. If a successful response lacks a supported `Location`, or durable binding fails, the route replaces that unusable success with one `503 realtime_call_binding_failed` and never replays the already-created call.

### Store an API-key-scoped opaque owner

The reserved key is `\ncodex_live_call:` plus SHA-256 over the proxy-key id, a NUL byte, and the normalized call id. Only that digest and owner id enter the existing sticky-session table. Raw call ids, proxy keys, OAuth tokens, SDP, attestation, and frames never do.

Insertion is immutable. Resolution expires rows after two hours. Successful binds opportunistically remove at most 250 expired reserved rows no more than once per five minutes per process. The namespace cannot be produced by ordinary sticky-session keys. Repository listings exclude it, and dashboard single, bulk, and filtered delete operations reject or skip it, so internal continuity state cannot appear as or be mutated like a user session.

### Normalize routes at the edge

Thin adapters accept bounded ASCII `rtc_...` or canonical UUID ids and select an explicit protocol before entering one auth, owner lookup, policy, lease, relay, and connector service:

- current-app `/backend-api/codex/{call_id}` and v3 `/v1/live/{call_id}` select `/v1/live/{call_id}` upstream;
- legacy `/v1/realtime?call_id={call_id}` consumes exactly one downstream `call_id` and appends its normalized value once, after remaining ordered query pairs, to `/v1/realtime` upstream.

No route infers protocol from call-id syntax. Duplicate or missing legacy `call_id` values fail closed.

### Enforce hard ownership and fresh identity

The service resolves ownership under the caller's key, rechecks current account assignment, selects that exact continuity owner with fallback disabled, and acquires one reattach stream lease. It then reloads the owner from persistence so a call created after token refresh uses current credentials rather than a cached routing snapshot. Missing, reassigned, paused, deleted, capped, or otherwise unavailable owners fail closed. Attachment never refreshes or selects another account.

### Keep realtime transport isolated from Responses

The live connector preserves remaining ordered query fields plus supplied version-specific alpha value or absence, FedRAMP, residency, session/context, originator, and attestation headers. It replaces proxy authorization, account identity, and client-supplied installation identity; strips Responses-only beta values; and synthesizes neither `OpenAI-Beta` nor `Sec-WebSocket-Protocol`.

A routed definitive handshake response is not replayed through route fallback. `InvalidProxy` is caught before broader handshake failures: live sideband returns a fixed credential-safe message, while the existing Responses connector retains its established `InvalidProxy` mapping byte-for-byte. Capability-specific denials do not mark the account globally unhealthy.

### Own relay and observability exactly once

The relay forwards text and binary messages without parsing. Either peer close or handler cancellation cancels and awaits paired work, forwards only bounded valid close code/reason data, closes each owned peer at most once, and releases the stream lease once. Cancelled connection attempts close any returned client before propagating cancellation.

Call-creation SDP is excluded from payload traces. Live frames never enter the Responses archive. Request logs record `request_kind=realtime_live`, `transport=websocket`, and credential-safe route data while ASGI path/query logging is redacted. The dashboard response schema accepts that producer value as a typed request row.

## Risks / Trade-offs

- **Private protocol drift:** typed adapters and public-seam regressions isolate route changes without duplicating the service.
- **Created but unbindable call:** fail closed before exposing the upstream success; do not retry across accounts.
- **Expired reconnect:** a call older than the fixed lifetime must be recreated.
- **Reserved-row accumulation:** fixed expiry plus throttled bounded cleanup limits hot-path work without a scheduler.
- **Capability-specific denial:** preserve status and credential-safe context without account penalty or replay.
- **Dashboard visibility:** the parser correction is intentionally dashboard-visible and therefore subject to P5 evidence requirements; correctness is not weakened to avoid that gate.

## Migration Plan

No schema, configuration, or dependency migration is required. Ship ownership, all route adapters, transport, dashboard contract, and regressions together. On rollback the routes disappear; reserved rows are inert and bounded by expiry. The OpenSpec change remains active through merge.

## Open Questions

None.
