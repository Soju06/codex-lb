## Context

Codex Live Voice has two account-coupled control legs: an HTTP WebRTC call-creation request and a sideband WebSocket. The generic Codex control proxy may refresh or fail over before a response becomes visible, so only the final successful account owns the returned call. An installed Codex app derives `WS /backend-api/codex/{call_id}` from a returned `/v1/realtime/calls/{call_id}` location. First-party Codex source also distinguishes v3 `/v1/live/{call_id}` from legacy v1/v2 `/v1/realtime?...&call_id={call_id}`. These are private compatibility routes, not the documented public Realtime API.

The authorized endpoint family includes the installed app's private `POST /backend-api/codex/realtime/calls` path and the related SDK-documented `POST /v1/realtime/calls` and `POST /v1/realtime/client_secrets` paths. The public SDK paths provide protocol context only: this change intentionally implements the private installed-app path and does not expose either public endpoint.

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
- Publish the private compatibility boundary in the docs site and sync the main `realtime-api-compat` capability.

**Non-Goals:**

- Public GPT-Live, public `POST /v1/realtime/calls`, or public `POST /v1/realtime/client_secrets` support.
- WebRTC media relay or SDP/frame/transcript/audio logging.
- Realtime event translation, parsing, authorization, or speculative subprotocol synthesis.
- A setting, migration, dependency, model-catalog entry, README section, `.env.example` line, dashboard navigation item, or new setup step.
- Any change to ordinary Responses WebSocket behavior.

## Decisions

### Require an existing proxy key without adding setup

The private call-create and sideband routes always resolve a registered proxy API key, even when ordinary proxy routes run with authentication disabled. Missing or invalid keys fail before account selection or upstream contact. This makes the private feature unavailable until an operator creates a key, but adds no required configuration: the base proxy and dashboard still work untouched, satisfying P1 through zero-config base behavior rather than by weakening private-route authorization.

A dedicated call-creation router is registered before the generic Codex router so the generic auth dependency cannot shadow this stricter route contract.

### Capture only the final successful control account

`codex_control_request` invokes an async success gate with the final account id and response on every successful return path, including initial success, pre-visible failover, and forced-refresh success. The realtime adapter durably binds ownership inside that gate, after upstream account success handling but before the single request-log disposition is chosen. It extracts the call id from the exact supported `Location` path before the first `?`, ignoring private query or fragment context. If a successful response lacks a supported `Location`, or durable binding fails, the gate returns a fixed failure disposition without raising into retry handling; the request row remains an error, the adapter replaces the unusable success with one `503 realtime_call_binding_failed`, and the already-created call is never replayed.

The realtime call adapter also selects a typed account-safe request privacy policy. Its warning/error records retain a fixed branch reason and request correlation while redacting internal account identifiers and suppressing exception text or traceback; ordinary Codex control adapters retain the default diagnostic policy. The policy reaches caller-local AuthManager metadata work. Because token refresh uses a process-global singleflight that private and ordinary callers can join in either order, task-internal refresh diagnostics always use the stricter content-free mode instead of inheriting whichever caller created the shared task.

### Store an API-key-scoped opaque owner

The reserved key is `\ncodex_live_call:` plus SHA-256 over the proxy-key id, a NUL byte, and the normalized call id. Only that digest and owner id enter the existing sticky-session table. Raw call ids, proxy keys, OAuth tokens, SDP, attestation, and frames never do.

Insertion is immutable. Resolution expires rows after two hours with a compare-and-delete conditioned on the owner and timestamp observed as expired, so it cannot delete a concurrently renewed binding. Successful binds opportunistically remove at most 250 expired reserved rows no more than once per five minutes per process. The namespace cannot be produced by ordinary sticky-session keys. Repository listings exclude it, and dashboard single, bulk, and filtered delete operations reject or skip it, so internal continuity state cannot appear as or be mutated like a user session.

### Normalize routes at the edge

Thin adapters accept bounded ASCII `rtc_...` or canonical UUID ids and select an explicit protocol before entering one auth, owner lookup, policy, lease, relay, and connector service. The current-app route is registered only for those two call-id families, so unrelated one-segment Codex WebSocket paths remain on ordinary Codex routing. Path adapters reject any downstream `call_id` query before owner lookup:

- current-app `/backend-api/codex/{call_id}` and v3 `/v1/live/{call_id}` select `/v1/live/{call_id}` upstream;
- legacy `/v1/realtime?call_id={call_id}` consumes exactly one downstream `call_id` and appends its normalized value once, after remaining ordered query pairs, to `/v1/realtime` upstream.

No route infers protocol from call-id syntax. Duplicate or missing legacy `call_id` values fail closed.

### Enforce hard ownership and fresh identity

The service resolves ownership under the caller's key, rechecks current account assignment, selects that exact continuity owner with fallback disabled, and acquires one reattach stream lease. It then reloads the owner from persistence so a call created after token refresh uses current credentials rather than a cached routing snapshot. Missing, reassigned, deleted, capped, or any non-active owner (`rate_limited`, `quota_exceeded`, `paused`, `reauth_required`, or `deactivated`) fails closed. Attachment never refreshes or selects another account.

### Keep realtime transport isolated from Responses

The live connector preserves remaining ordered query fields plus supplied version-specific alpha value or absence, FedRAMP, residency, session/context, originator, and attestation headers. It replaces proxy authorization, account identity, and client-supplied installation identity; strips Responses-only beta values; and synthesizes neither `OpenAI-Beta` nor `Sec-WebSocket-Protocol`. Ordered downstream subprotocol offers travel only through each transport's negotiation argument, and downstream is accepted only with the upstream-selected value when that exact value was offered.

A routed definitive handshake response or network failure is not replayed through route fallback for the live sideband. Direct `InvalidProxy`, `InvalidHandshake`, and `OSError` failures return fixed credential-safe Live messages, while the existing Responses connector retains its established exception behavior and default routed network fallback unchanged. Capability-specific denials do not mark the account globally unhealthy.

### Own relay and observability exactly once

The relay forwards text and binary messages without parsing. Either peer close or handler cancellation cancels and awaits paired work, forwards only bounded valid close code/reason data, closes each owned peer at most once, and releases the stream lease once. Upstream close has both an initial timeout and a separate fixed post-cancel drain cap; if transport cleanup ignores cancellation, the handler stops awaiting it, consumes its eventual task result, and continues to lease release. Cancelled connection attempts close any returned client before propagating cancellation.

Call-creation SDP is excluded from payload traces. Live frames never enter the Responses archive. Persisted private request rows omit account identity, model content, upstream error text, and failure metadata. Sideband rows record `request_kind=realtime_live`, `transport=websocket`, and credential-safe route data while the shared ASGI scope makes the actual Uvicorn accepted-handshake log emit a redacted path with no query. The dashboard response schema accepts that producer value as a typed request row.

## Risks / Trade-offs

- **Private protocol drift:** typed adapters and public-seam regressions isolate route changes without duplicating the service.
- **Created but unbindable call:** fail closed before exposing the upstream success; do not retry across accounts.
- **Expired reconnect:** a call older than the fixed lifetime must be recreated.
- **Reserved-row accumulation:** fixed expiry plus throttled bounded cleanup limits hot-path work without a scheduler.
- **Capability-specific denial:** preserve status and credential-safe context without account penalty or replay.
- **Dashboard visibility:** accept the backend's typed `realtime_live` value so live-voice request rows remain visible without weakening the field to arbitrary strings.

## Migration Plan

No schema, configuration, or dependency migration is required. Ship ownership, all route adapters, transport, dashboard contract, and regressions together. On rollback the routes disappear; reserved rows are inert and bounded by expiry. The OpenSpec change remains active through merge.

## Open Questions

None.
